import requests
import pandas as pd
from datetime import datetime, timedelta
import json
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Configuración desde variables de entorno (GitHub Secrets)
ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
AD_ACCOUNT_ID = f"act_{os.environ.get('META_ACCOUNT_ID', '')}"
DIAS_HISTORICO = 30
UMBRAL_ANOMALIA = 1.5
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
ALERT_EMAILS = os.environ.get("ALERT_EMAILS", "").split(",")

def obtener_datos_meta(dias=30):
    print(f"Descargando datos de los últimos {dias} días desde Meta Ads...")
    fecha_fin = datetime.now() - timedelta(hours=1)
    fecha_inicio = fecha_fin - timedelta(days=dias)
    url = f"https://graph.facebook.com/v19.0/{AD_ACCOUNT_ID}/insights"
    params = {
        "access_token": ACCESS_TOKEN,
        "fields": "campaign_name,spend,impressions,clicks",
        "time_increment": "1",
        "breakdowns": "hourly_stats_aggregated_by_advertiser_time_zone",
        "time_range": json.dumps({
            "since": fecha_inicio.strftime("%Y-%m-%d"),
            "until": fecha_fin.strftime("%Y-%m-%d")
        }),
        "level": "campaign",
        "limit": 500
    }
    todos = []
    first = True
    while url:
        r = requests.get(url, params=params if first else {})
        first = False
        data = r.json()
        if "error" in data:
            print(f"Error de API: {data['error']['message']}")
            return None
        todos.extend(data.get("data", []))
        url = data.get("paging", {}).get("next")
        print(f"  Descargados {len(todos)} registros...")
    return todos

def procesar_datos(datos_raw):
    print("Procesando datos...")
    registros = []
    for row in datos_raw:
        fecha = row.get("date_start", "")[:10]
        hora_str = row.get("hourly_stats_aggregated_by_advertiser_time_zone", "0:00 AM - 1:00 AM")
        try:
            h = int(hora_str.split(":")[0])
            if "PM" in hora_str and h != 12:
                h += 12
            elif "AM" in hora_str and h == 12:
                h = 0
        except:
            h = 0
        registros.append({
            "fecha": fecha,
            "hora": h,
            "campana": row.get("campaign_name", ""),
            "gasto": float(row.get("spend", 0)),
            "impresiones": int(row.get("impressions", 0)),
            "clics": int(row.get("clicks", 0))
        })
    return pd.DataFrame(registros)

def detectar_anomalias(df):
    print("Detectando anomalías...")
    hourly = df.groupby(["fecha", "hora"])["gasto"].sum().reset_index()
    todas_fechas = df["fecha"].unique()
    idx_completo = pd.MultiIndex.from_product([todas_fechas, range(24)], names=["fecha", "hora"])
    hourly = hourly.set_index(["fecha", "hora"]).reindex(idx_completo, fill_value=0).reset_index()
    stats = hourly.groupby("hora")["gasto"].agg(["mean", "std"]).reset_index()
    stats.columns = ["hora", "mean", "std"]
    stats["std"] = stats["std"].fillna(0)
    merged = hourly.merge(stats, on="hora")
    merged["lower"] = (merged["mean"] - UMBRAL_ANOMALIA * merged["std"]).clip(lower=0)
    merged["upper"] = merged["mean"] + UMBRAL_ANOMALIA * merged["std"]
    merged["gasto"] = merged["gasto"].fillna(0)
    hora_actual_global = datetime.now().hour
    fecha_hoy_global = datetime.now().strftime("%Y-%m-%d")
    merged["es_anomalia"] = (
      ((merged["gasto"] < merged["lower"]) | (merged["gasto"] < 0.01)) &
      ~((merged["fecha"] == fecha_hoy_global) & (merged["hora"] >= hora_actual_global))
    )
    merged["pct"] = ((merged["gasto"] - merged["mean"]) / merged["mean"].replace(0, 1) * 100).round(1)
    return merged, stats

def enviar_alerta_email(anomalias_recientes):
    if not GMAIL_USER or not GMAIL_APP_PASSWORD or not ALERT_EMAILS:
        print("Email no configurado, saltando alertas.")
        return

    if anomalias_recientes.empty:
        print("Sin anomalías recientes, no se envía alerta.")
        return

    print(f"Enviando alerta a {len(ALERT_EMAILS)} destinatarios...")

    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    total = len(anomalias_recientes)

    filas_html = ""
    for _, row in anomalias_recientes.iterrows():
        severidad = "🔴 Crítica" if row["pct"] <= -80 else "🟠 Alta"
        gasto_color = "#ff4d4d" if row["gasto"] < 0.01 else "#f5a623"
        filas_html += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #333;font-family:monospace">{row['fecha']}</td>
            <td style="padding:8px;border-bottom:1px solid #333;font-family:monospace">{int(row['hora'])}:00–{int(row['hora'])+1}:00</td>
            <td style="padding:8px;border-bottom:1px solid #333;color:{gasto_color};font-family:monospace">${row['gasto']:.2f}</td>
            <td style="padding:8px;border-bottom:1px solid #333;color:#888;font-family:monospace">${row['mean']:.2f}</td>
            <td style="padding:8px;border-bottom:1px solid #333;color:#ff4d4d;font-weight:bold">{row['pct']:.0f}%</td>
            <td style="padding:8px;border-bottom:1px solid #333">{severidad}</td>
        </tr>"""

    html_body = f"""
    <html>
    <body style="background:#0f0f0f;color:#f0ede8;font-family:'DM Sans',sans-serif;padding:24px">
      <div style="max-width:700px;margin:0 auto">
        <div style="background:#ff4d4d;padding:12px 20px;border-radius:8px;margin-bottom:20px;display:inline-block">
          <span style="color:white;font-weight:600;font-size:14px">⚡ ALERTA DE ANOMALÍAS — Meta Ads</span>
        </div>
        <h2 style="color:#f0ede8;margin-bottom:4px">Pollo Campero Guatemala</h2>
        <p style="color:#8a8680;margin-bottom:20px">Detectadas {total} anomalías · {now}</p>
        <table style="width:100%;border-collapse:collapse;background:#161616;border-radius:8px;overflow:hidden">
          <thead>
            <tr style="background:#1e1e1e">
              <th style="padding:10px 8px;text-align:left;color:#5a5754;font-size:11px;text-transform:uppercase">Fecha</th>
              <th style="padding:10px 8px;text-align:left;color:#5a5754;font-size:11px;text-transform:uppercase">Hora</th>
              <th style="padding:10px 8px;text-align:left;color:#5a5754;font-size:11px;text-transform:uppercase">Gasto real</th>
              <th style="padding:10px 8px;text-align:left;color:#5a5754;font-size:11px;text-transform:uppercase">Esperado</th>
              <th style="padding:10px 8px;text-align:left;color:#5a5754;font-size:11px;text-transform:uppercase">Desviación</th>
              <th style="padding:10px 8px;text-align:left;color:#5a5754;font-size:11px;text-transform:uppercase">Severidad</th>
            </tr>
          </thead>
          <tbody>{filas_html}</tbody>
        </table>
        <p style="color:#5a5754;font-size:11px;margin-top:20px">Monitor de Anomalías · Meta Ads · LDM Agency</p>
      </div>
    </body>
    </html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"⚠️ {total} anomalías detectadas — Campero GT Meta Ads {now}"
    msg["From"] = GMAIL_USER
    msg["To"] = ", ".join(ALERT_EMAILS)
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD.replace(" ", ""))
            server.sendmail(GMAIL_USER, ALERT_EMAILS, msg.as_string())
        print("✅ Alerta enviada correctamente")
    except Exception as e:
        print(f"❌ Error enviando email: {e}")

def generar_dashboard(df, merged, stats):
    print("Generando dashboard...")
    anomalias = merged[merged["es_anomalia"]].sort_values(["fecha", "hora"])
    daily = merged.groupby("fecha").agg(
        gasto=("gasto", "sum"),
        anomalias=("es_anomalia", "sum")
    ).reset_index()
    daily_dict = {row["fecha"]: {"gasto": round(row["gasto"], 2), "anomalias": int(row["anomalias"])} for _, row in daily.iterrows()}
    dates = sorted(daily_dict.keys())
    hourly_by_date = {}
    for fecha in dates:
        day_data = merged[merged["fecha"] == fecha].sort_values("hora")
        vals = [0.0] * 24
        for _, row in day_data.iterrows():
            h = int(row["hora"])
            if 0 <= h < 24:
                vals[h] = round(row["gasto"], 2)
        hourly_by_date[fecha] = vals
    hour_stats_list = []
    for _, row in stats.iterrows():
        std = float(row["std"]) if row["std"] > 0 else 0
        hour_stats_list.append({
            "h": int(row["hora"]),
            "mean": round(float(row["mean"]), 2),
            "lo": round(max(0, float(row["mean"]) - UMBRAL_ANOMALIA * std), 2),
            "hi": round(float(row["mean"]) + UMBRAL_ANOMALIA * std, 2)
        })
    anomalias_list = []
    for _, row in anomalias.iterrows():
        anomalias_list.append({
            "fecha": row["fecha"],
            "hora": int(row["hora"]),
            "gasto": round(float(row["gasto"]), 2),
            "mean": round(float(row["mean"]), 2),
            "lo": round(float(row["lower"]), 2),
            "pct": round(float(row["pct"]), 1)
        })
    total_gasto = sum(d["gasto"] for d in daily_dict.values())
    total_anomalias = sum(d["anomalias"] for d in daily_dict.values())
    peor_dia = min(daily_dict.keys(), key=lambda x: daily_dict[x]["gasto"]) if daily_dict else ""
    peor_dia_gasto = daily_dict.get(peor_dia, {}).get("gasto", 0)
    promedio_diario = total_gasto / max(len(dates), 1)
    now = datetime.now().strftime("%d/%m/%Y %H:%M")

    daily_json = json.dumps(daily_dict)
    dates_json = json.dumps(dates)
    hour_stats_json = json.dumps(hour_stats_list)
    hourly_json = json.dumps(hourly_by_date)
    anomalies_json = json.dumps(anomalias_list)

    html = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Monitor de Campañas — Meta Ads</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&display=swap');
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  :root{--bg:#0f0f0f;--bg2:#161616;--bg3:#1e1e1e;--border:rgba(255,255,255,0.08);--border2:rgba(255,255,255,0.14);--text:#f0ede8;--text2:#8a8680;--text3:#5a5754;--red:#ff4d4d;--amber:#f5a623;--green:#4caf7d;--blue:#5b9cf6}
  body{font-family:'DM Sans',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
  .topbar{background:var(--bg2);border-bottom:1px solid var(--border);padding:16px 32px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
  .topbar-left{display:flex;align-items:center;gap:14px}
  .logo{width:32px;height:32px;background:var(--red);border-radius:8px;display:flex;align-items:center;justify-content:center}
  .logo svg{width:18px;height:18px;fill:white}
  .topbar-title{font-size:14px;font-weight:500}
  .topbar-sub{font-size:12px;color:var(--text2);margin-top:1px}
  .tag{font-size:11px;font-family:'DM Mono',monospace;padding:4px 10px;border-radius:20px;font-weight:500}
  .tag-red{background:rgba(255,77,77,0.1);color:var(--red);border:1px solid rgba(255,77,77,0.2)}
  .tag-green{background:rgba(76,175,125,0.08);color:var(--green);border:1px solid rgba(76,175,125,0.2)}
  .tag-amber{background:rgba(245,166,35,0.1);color:var(--amber);border:1px solid rgba(245,166,35,0.2)}
  .main{padding:28px 32px;max-width:1280px}
  .metrics-row{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:24px}
  .metric-card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:16px 18px}
  .metric-card.danger{border-color:rgba(255,77,77,0.25);background:rgba(255,77,77,0.04)}
  .metric-card.warning{border-color:rgba(245,166,35,0.2)}
  .metric-label{font-size:11px;font-weight:500;color:var(--text3);text-transform:uppercase;letter-spacing:0.08em;margin-bottom:8px}
  .metric-value{font-size:26px;font-weight:600;line-height:1}
  .metric-value.red{color:var(--red)}
  .metric-value.amber{color:var(--amber)}
  .metric-sub{font-size:11px;color:var(--text2);margin-top:5px}
  .row{display:grid;gap:16px;margin-bottom:16px}
  .row-2{grid-template-columns:1fr 1fr}
  .row-3-1{grid-template-columns:2fr 1fr}
  .card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:20px 22px}
  .card-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
  .card-title{font-size:13px;font-weight:500}
  .card-sub{font-size:12px;color:var(--text2);margin-top:2px}
  select{font-family:'DM Sans',sans-serif;font-size:12px;background:var(--bg3);color:var(--text);border:1px solid var(--border2);border-radius:8px;padding:6px 10px;cursor:pointer;outline:none}
  .legend{display:flex;gap:16px;margin-bottom:14px;flex-wrap:wrap}
  .legend-item{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--text2)}
  .legend-dot{width:8px;height:8px;border-radius:2px;flex-shrink:0}
  table{width:100%;border-collapse:collapse;font-size:12px}
  thead th{text-align:left;padding:8px 10px;font-size:11px;font-weight:500;color:var(--text3);text-transform:uppercase;letter-spacing:0.07em;border-bottom:1px solid var(--border)}
  tbody tr{border-bottom:1px solid var(--border)}
  tbody tr:last-child{border-bottom:none}
  tbody tr:hover{background:rgba(255,255,255,0.025)}
  tbody td{padding:9px 10px}
  .badge{display:inline-block;font-size:10px;font-weight:500;padding:3px 8px;border-radius:20px;font-family:'DM Mono',monospace}
  .badge-crit{background:rgba(255,77,77,0.15);color:#ff7070;border:1px solid rgba(255,77,77,0.2)}
  .badge-high{background:rgba(245,166,35,0.12);color:var(--amber);border:1px solid rgba(245,166,35,0.2)}
  .day-grid{display:grid;grid-template-columns:repeat(10,1fr);gap:6px}
  .day-cell{aspect-ratio:1;border-radius:6px;display:flex;flex-direction:column;align-items:center;justify-content:center;cursor:pointer;border:1px solid transparent;transition:all 0.15s;font-size:11px}
  .day-cell:hover{border-color:var(--border2);transform:scale(1.05)}
  .day-num{font-weight:500;line-height:1}
  .day-anom{font-size:9px;margin-top:2px;font-family:'DM Mono',monospace}
  .day-cell.ok{background:rgba(76,175,125,0.08);color:var(--green)}
  .day-cell.warn{background:rgba(245,166,35,0.1);color:var(--amber)}
  .day-cell.crit{background:rgba(255,77,77,0.1);color:var(--red)}
  .day-cell.selected{border-color:rgba(255,255,255,0.4)!important}
  .scrollable{max-height:320px;overflow-y:auto}
  .footer{padding:20px 32px;border-top:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;font-size:11px;color:var(--text3);margin-top:8px}
</style>
</head>
<body>
<div class="topbar">
  <div class="topbar-left">
    <div class="logo"><svg viewBox="0 0 24 24"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg></div>
    <div>
      <div class="topbar-title">Monitor de Anomalías — Meta Ads</div>
      <div class="topbar-sub">Pollo Campero Guatemala · Actualizado: """ + now + """</div>
    </div>
  </div>
  <div style="display:flex;gap:8px;">
    <span class="tag tag-red">""" + str(total_anomalias) + """ anomalías</span>
    <span class="tag tag-green">""" + str(len(dates)) + """ días</span>
    <span class="tag tag-amber">Últimos """ + str(DIAS_HISTORICO) + """ días</span>
  </div>
</div>
<div class="main">
  <div class="metrics-row">
    <div class="metric-card"><div class="metric-label">Gasto total</div><div class="metric-value">$""" + f"{total_gasto:,.2f}" + """</div><div class="metric-sub">Últimos """ + str(DIAS_HISTORICO) + """ días</div></div>
    <div class="metric-card"><div class="metric-label">Promedio diario</div><div class="metric-value">$""" + f"{promedio_diario:,.2f}" + """</div><div class="metric-sub">por día</div></div>
    <div class="metric-card warning"><div class="metric-label">Anomalías</div><div class="metric-value amber">""" + str(total_anomalias) + """</div><div class="metric-sub">horas anómalas detectadas</div></div>
    <div class="metric-card danger"><div class="metric-label">Peor día</div><div class="metric-value red">""" + peor_dia + """</div><div class="metric-sub">$""" + f"{peor_dia_gasto:,.2f}" + """ gastado</div></div>
    <div class="metric-card"><div class="metric-label">Días analizados</div><div class="metric-value">""" + str(len(dates)) + """</div><div class="metric-sub">con desglose horario</div></div>
  </div>
  <div class="row row-3-1">
    <div class="card">
      <div class="card-header">
        <div><div class="card-title">Consumo por hora</div><div class="card-sub" id="chartSub">Selecciona un día</div></div>
        <select id="dateSelect"></select>
      </div>
      <div class="legend">
        <div class="legend-item"><div class="legend-dot" style="background:#5b9cf6"></div>Gasto real</div>
        <div class="legend-item"><div class="legend-dot" style="background:rgba(245,166,35,0.5);border:1px solid #f5a623"></div>Rango normal</div>
        <div class="legend-item"><div class="legend-dot" style="background:#ff4d4d"></div>Anomalía</div>
      </div>
      <div style="position:relative;width:100%;height:240px"><canvas id="hourlyChart"></canvas></div>
    </div>
    <div class="card">
      <div class="card-header"><div class="card-title">Calendario de anomalías</div></div>
      <div class="day-grid" id="dayGrid"></div>
      <div style="display:flex;gap:10px;margin-top:14px;flex-wrap:wrap">
        <div class="legend-item"><div class="legend-dot" style="background:rgba(76,175,125,0.08);border:1px solid #4caf7d"></div><span style="color:#8a8680;font-size:11px">Sin anomalías</span></div>
        <div class="legend-item"><div class="legend-dot" style="background:rgba(245,166,35,0.1);border:1px solid #f5a623"></div><span style="color:#8a8680;font-size:11px">1–3 anomalías</span></div>
        <div class="legend-item"><div class="legend-dot" style="background:rgba(255,77,77,0.1);border:1px solid #ff4d4d"></div><span style="color:#8a8680;font-size:11px">4+ anomalías</span></div>
      </div>
    </div>
  </div>
  <div class="row row-2">
    <div class="card">
      <div class="card-header"><div class="card-title">Gasto diario total</div><div class="card-sub">Rojo = días con anomalías</div></div>
      <div style="position:relative;width:100%;height:180px"><canvas id="dailyChart"></canvas></div>
    </div>
    <div class="card">
      <div class="card-header"><div class="card-title">Patrón normal de consumo</div><div class="card-sub">Promedio histórico por hora</div></div>
      <div style="position:relative;width:100%;height:180px"><canvas id="patternChart"></canvas></div>
    </div>
  </div>
  <div class="card">
    <div class="card-header"><div class="card-title">Registro de anomalías detectadas</div><div class="card-sub">Horas donde el consumo cayó más de """ + str(UMBRAL_ANOMALIA) + """ desviaciones estándar o fue $0</div></div>
    <div class="scrollable"><table>
      <thead><tr><th>Fecha</th><th>Hora</th><th>Gasto real</th><th>Esperado</th><th>Límite mínimo</th><th>Desviación</th><th>Severidad</th></tr></thead>
      <tbody id="anomalyBody"></tbody>
    </table></div>
  </div>
</div>
<div class="footer">
  <span>Monitor de Anomalías · Meta Ads · LDM Agency</span>
  <span>Metodología: Media ± """ + str(UMBRAL_ANOMALIA) + """σ por hora · """ + str(DIAS_HISTORICO) + """ días de historial</span>
</div>
<script>
const DAILY = """ + daily_json + """;
const DATES = """ + dates_json + """;
const HOUR_STATS = """ + hour_stats_json + """;
const HOURLY = """ + hourly_json + """;
const ANOMALIES = """ + anomalies_json + """;
let sel = DATES[DATES.length-1];
let hChart = null;
const ds = document.getElementById('dateSelect');
DATES.forEach(d => {
  const o = document.createElement('option');
  o.value = d;
  const a = DAILY[d] ? DAILY[d].anomalias : 0;
  o.textContent = d + (a > 0 ? ' (' + a + ' ⚠)' : '');
  if (d === sel) o.selected = true;
  ds.appendChild(o);
});
ds.addEventListener('change', e => { sel = e.target.value; updateHourly(); updateGrid(); });
function updateHourly() {
  const hrs = Array.from({length:24},(_,i)=>i);
  const day = HOURLY[sel] || Array(24).fill(0);
  const aSet = new Set(ANOMALIES.filter(a=>a.fecha===sel).map(a=>a.hora));
  const colors = hrs.map(h => aSet.has(h) ? '#ff4d4d' : '#5b9cf6');
  const sub = document.getElementById('chartSub');
  const an = DAILY[sel] ? DAILY[sel].anomalias : 0;
  sub.textContent = sel + ' · ' + (an > 0 ? an + ' anomalías detectadas' : 'sin anomalías');
  const ctx = document.getElementById('hourlyChart').getContext('2d');
  if (hChart) hChart.destroy();
  hChart = new Chart(ctx, {
    type: 'bar',
    data: { labels: hrs.map(h=>h+'h'), datasets: [
      {label:'Superior',data:HOUR_STATS.map(s=>s.hi),type:'line',borderColor:'rgba(245,166,35,0.6)',backgroundColor:'rgba(245,166,35,0.07)',fill:'+1',pointRadius:0,borderWidth:1.5,borderDash:[3,3],tension:0.3,order:1},
      {label:'Inferior',data:HOUR_STATS.map(s=>s.lo),type:'line',borderColor:'rgba(245,166,35,0.6)',fill:false,pointRadius:0,borderWidth:1.5,borderDash:[3,3],tension:0.3,order:1},
      {label:'Gasto',data:day,backgroundColor:colors,borderRadius:3,order:2}
    ]},
    options: {responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>c.dataset.label==='Gasto'?' $'+(c.raw||0).toFixed(2)+(aSet.has(c.dataIndex)?' ⚠ ANOMALÍA':''):' $'+(c.raw||0).toFixed(2)}}},scales:{x:{grid:{display:false},ticks:{color:'#5a5754',font:{size:10}}},y:{ticks:{callback:v=>'$'+v,color:'#5a5754',font:{size:10}},grid:{color:'rgba(255,255,255,0.05)'},border:{display:false}}}}
  });
}
function updateGrid() {
  const g = document.getElementById('dayGrid');
  g.innerHTML = '';
  DATES.forEach(d => {
    const a = DAILY[d] ? DAILY[d].anomalias : 0;
    const cls = a===0?'ok':a<=3?'warn':'crit';
    const c = document.createElement('div');
    c.className = 'day-cell ' + cls + (d===sel?' selected':'');
    c.innerHTML = '<span class="day-num">'+d.slice(8)+'</span><span class="day-anom">'+(a>0?a+'⚠':'✓')+'</span>';
    c.addEventListener('click', () => { sel=d; ds.value=d; updateHourly(); updateGrid(); });
    g.appendChild(c);
  });
}
(function(){
  const ctx = document.getElementById('dailyChart').getContext('2d');
  new Chart(ctx, {type:'bar',data:{labels:DATES.map(d=>d.slice(5)),datasets:[{label:'Gasto',data:DATES.map(d=>DAILY[d]?DAILY[d].gasto:0),backgroundColor:DATES.map(d=>DAILY[d]&&DAILY[d].anomalias>0?'#ff4d4d':'#5b9cf6'),borderRadius:3}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{grid:{display:false},ticks:{color:'#5a5754',font:{size:10},maxRotation:45}},y:{ticks:{callback:v=>'$'+v,color:'#5a5754',font:{size:10}},grid:{color:'rgba(255,255,255,0.05)'},border:{display:false}}}}});
})();
(function(){
  const ctx = document.getElementById('patternChart').getContext('2d');
  new Chart(ctx, {type:'line',data:{labels:HOUR_STATS.map(s=>s.h+'h'),datasets:[{label:'Superior',data:HOUR_STATS.map(s=>s.hi),borderColor:'rgba(245,166,35,0.4)',backgroundColor:'rgba(245,166,35,0.07)',fill:'+1',pointRadius:0,borderWidth:1,borderDash:[3,3],tension:0.4},{label:'Inferior',data:HOUR_STATS.map(s=>s.lo),borderColor:'rgba(245,166,35,0.4)',fill:false,pointRadius:0,borderWidth:1,borderDash:[3,3],tension:0.4},{label:'Promedio',data:HOUR_STATS.map(s=>s.mean),borderColor:'#4caf7d',fill:false,pointRadius:0,borderWidth:2,tension:0.4}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{grid:{display:false},ticks:{color:'#5a5754',font:{size:10}}},y:{ticks:{callback:v=>'$'+v,color:'#5a5754',font:{size:10}},grid:{color:'rgba(255,255,255,0.05)'},border:{display:false}}}}});
})();
(function(){
  const tb = document.getElementById('anomalyBody');
  ANOMALIES.forEach(a => {
    const s = a.pct <= -80 ? ['badge-crit','Crítica'] : ['badge-high','Alta'];
    const tr = document.createElement('tr');
    tr.innerHTML = '<td style="font-family:DM Mono,monospace;font-size:11px">'+a.fecha+'</td><td style="color:#8a8680;font-family:DM Mono,monospace;font-size:11px">'+a.hora+':00–'+(a.hora+1)+':00</td><td style="color:#ff4d4d;font-family:DM Mono,monospace;font-size:11px">$'+a.gasto.toFixed(2)+'</td><td style="color:#8a8680;font-family:DM Mono,monospace;font-size:11px">$'+a.mean.toFixed(2)+'</td><td style="color:#8a8680;font-family:DM Mono,monospace;font-size:11px">$'+a.lo.toFixed(2)+'</td><td style="color:#ff4d4d;font-family:DM Mono,monospace;font-size:11px;font-weight:500">'+a.pct.toFixed(0)+'%</td><td><span class="badge '+s[0]+'">'+s[1]+'</span></td>';
    tb.appendChild(tr);
  });
})();
updateHourly();
updateGrid();
</script>
</body>
</html>"""

    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n✅ Dashboard generado: {output_path}")
    print(f"📊 Total registros procesados: {len(df)}")
    print(f"🚨 Anomalías detectadas: {len(anomalias)}")
    print(f"📅 Días analizados: {len(dates)}")
    return output_path, anomalias

if __name__ == "__main__":
    print("=" * 50)
    print("MONITOR DE CAMPAÑAS META ADS — LDM")
    print("=" * 50)
    datos_raw = obtener_datos_meta(DIAS_HISTORICO)
    if datos_raw:
        df = procesar_datos(datos_raw)
        merged, stats = detectar_anomalias(df)
        ruta, anomalias = generar_dashboard(df, merged, stats)

        # Solo alertar por anomalías de las últimas 2 horas
        ahora = datetime.now()
        hace_2h = ahora - timedelta(hours=2)
        fecha_hoy = ahora.strftime("%Y-%m-%d")
        hora_actual = ahora.hour
        anomalias_recientes = anomalias[
               (anomalias["fecha"] == fecha_hoy) &
               (anomalias["hora"] >= max(0, hora_actual - 2)) &
               (anomalias["hora"] < hora_actual)
        ]

        if not anomalias_recientes.empty:
            print(f"\n⚠️ {len(anomalias_recientes)} anomalías recientes detectadas — enviando alerta...")
            enviar_alerta_email(anomalias_recientes)
        else:
            print("\n✅ Sin anomalías recientes en las últimas 2 horas")

        print(f"\nDashboard disponible en: {ruta}")
    else:
        print("No se pudieron obtener datos. Verifica tu token.")
