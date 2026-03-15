import os
import json
import webbrowser
from datetime import datetime, timezone
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
import isodate
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────
API_KEY            = os.getenv("API_KEY", "")
CHANNEL_ID         = os.getenv("CHANNEL_ID", "UCiFazFymzsTLHTA2dvEsa8A")
CLIENT_SECRET_FILE = os.getenv("CLIENT_SECRET_FILE", "client_secret.json")
SCOPES             = ["https://www.googleapis.com/auth/yt-analytics.readonly"]

START_DATE         = "2024-03-15"
END_DATE           = datetime.now(timezone.utc).date().strftime("%Y-%m-%d")
MONETIZATION_GOAL  = 4000          # horas requeridas para monetizar
MIN_DURATION_SEC   = 0             # Incluir todos los videos (incluyendo Shorts)
MAX_RESULTS        = 500           # Aumentar límite de videos analizados


from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# ─────────────────────────────────────────────
# AUTENTICACIÓN
# ─────────────────────────────────────────────
def authenticate():
    creds = None
    # 1. Intentar cargar desde token.json
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        
    # 2. Si no hay credenciales o expiraron
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request()) # Refrescar token automáticamente
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
            
        # 3. Guardar el nuevo token para futuras ejecuciones (como en Github Actions)
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    analytics = build("youtubeAnalytics", "v2", credentials=creds)
    youtube   = build("youtube", "v3", developerKey=API_KEY)
    return analytics, youtube


# ─────────────────────────────────────────────
# OBTENER ANALYTICS
# ─────────────────────────────────────────────
def get_analytics(analytics, start_date: str, end_date: str) -> list:
    response = analytics.reports().query(
        ids="channel==MINE",
        startDate=start_date,
        endDate=end_date,
        metrics="estimatedMinutesWatched,views,subscribersGained",
        dimensions="video",
        sort="-estimatedMinutesWatched",
        maxResults=MAX_RESULTS,
    ).execute()
    return response.get("rows", [])
    
def get_daily_reports(analytics, start_date: str, end_date: str) -> list:
    """Obtiene el rendimiento por día (horas, vistas)."""
    response = analytics.reports().query(
        ids="channel==MINE",
        startDate=start_date,
        endDate=end_date,
        metrics="estimatedMinutesWatched,views",
        dimensions="day",
        sort="day"
    ).execute()
    return response.get("rows", [])


# ─────────────────────────────────────────────
# OBTENER INFORMACIÓN DEL CANAL
# ─────────────────────────────────────────────
def get_channel_info(youtube) -> dict:
    res = youtube.channels().list(
        part="snippet,statistics",
        id=CHANNEL_ID
    ).execute()

    if not res.get("items"):
        return {"title": "Canal de YouTube", "thumb": "", "subs": 0}

    item = res["items"][0]
    return {
        "title": item["snippet"]["title"],
        "thumb": item["snippet"]["thumbnails"].get("high", item["snippet"]["thumbnails"].get("default", {}))["url"],
        "subs":  int(item["statistics"].get("subscriberCount", 0))
    }


# ─────────────────────────────────────────────
# OBTENER DETALLES DE VIDEOS
# ─────────────────────────────────────────────
def get_video_details(youtube, video_ids: list, start_date_dt: datetime) -> dict:
    info = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        res   = youtube.videos().list(
            part="snippet,contentDetails,statistics",
            id=",".join(batch),
        ).execute()

        for item in res.get("items", []):
            vid      = item["id"]
            seconds  = isodate.parse_duration(item["contentDetails"]["duration"]).total_seconds()
            published_str  = item["snippet"]["publishedAt"][:10]
            published_date = datetime.strptime(published_str, "%Y-%m-%d")

            if seconds >= MIN_DURATION_SEC:
                info[vid] = {
                    "title":     item["snippet"]["title"],
                    "published": published_str,
                    "duration":  round(seconds / 60, 2),
                    "likes":     int(item["statistics"].get("likeCount", 0)),
                    "comments":  int(item["statistics"].get("commentCount", 0))
                }
    return info


# ─────────────────────────────────────────────
# PROCESAR MÉTRICAS
# ─────────────────────────────────────────────
def process_videos(rows: list, info: dict) -> list:
    hoy = datetime.now(timezone.utc)
    result = []

    for row in rows:
        vid, estimated_minutes, views, subs_gained = row[0], row[1], row[2], row[3] if len(row) > 3 else 0

        if vid not in info:
            continue

        data             = info[vid]
        duration         = data["duration"]
        hours            = estimated_minutes / 60
        minutes_per_view = estimated_minutes / views if views else 0
        retention        = (minutes_per_view / duration) * 100 if duration else 0

        published    = datetime.strptime(data["published"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        days_online  = max((hoy - published).days + 1, 1)
        views_per_day = views / days_online
        
        # Calcular Engagement (Likes + Comentarios / Vistas * 100)
        likes = data["likes"]
        comments = data["comments"]
        engagement = ((likes + comments) / views) * 100 if views > 0 else 0

        result.append({
            "vid":       vid,
            "title":     data["title"],
            "published": data["published"],
            "duration":  duration,
            "views":     views,
            "minutes":   estimated_minutes,
            "hours":     round(hours, 2),
            "min_view":  round(minutes_per_view, 2),
            "retention": round(retention, 1),
            "views_day": round(views_per_day, 1),
            "subs":      subs_gained,
            "likes":     likes,
            "comments":  comments,
            "engagement": round(engagement, 2),
            "thumb":     f"https://img.youtube.com/vi/{vid}/mqdefault.jpg",
        })

    return result


# ─────────────────────────────────────────────
# GENERAR HTML  (versión premium con Chart.js)
# ─────────────────────────────────────────────
def generate_html(videos: list, goal: float, channel_info: dict, daily_data: dict) -> str:
    generated_at = datetime.now().strftime("%d/%m/%Y %H:%M")

    # Build JSON for the JS data block
    js_videos = json.dumps([
        {
            "vid":       v["vid"],
            "title":     v["title"],
            "published": v["published"],
            "duration":  v["duration"],
            "views":     v["views"],
            "minutes":   v["minutes"],
            "hours":     v["hours"],
            "min_view":  v["min_view"],
            "retention": v["retention"],
            "views_day": v["views_day"],
            "subs":      v["subs"],
            "likes":     v["likes"],
            "comments":  v["comments"],
            "engagement":v["engagement"],
        }
        for v in videos
    ], ensure_ascii=False, indent=2)

    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template.html")
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()

    # Reemplazar placeholders
    html = html.replace("[[START_DATE]]", START_DATE)
    html = html.replace("[[END_DATE]]", END_DATE)
    html = html.replace("[[GENERATED_AT]]", generated_at)
    html = html.replace("[[GOAL]]", str(goal))
    html = html.replace("[[GOAL_INT]]", f"{int(goal):,}")
    html = html.replace("[[JS_VIDEOS]]", js_videos)
    html = html.replace("[[CHANNEL_TITLE]]", channel_info["title"])
    html = html.replace("[[CHANNEL_THUMB]]", channel_info["thumb"])
    html = html.replace("[[CHANNEL_SUBS]]", f"{channel_info['subs']:,}")
    html = html.replace("[[JS_DAILY_METRICS]]", json.dumps(daily_data, ensure_ascii=False))

    return html


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    print("🔐 Autenticando...")
    analytics, youtube = authenticate()

    print(f"📡 Obteniendo analytics ({START_DATE} → {END_DATE})...")
    rows      = get_analytics(analytics, START_DATE, END_DATE)
    video_ids = [r[0] for r in rows]

    start_date_dt = datetime.strptime(START_DATE, "%Y-%m-%d")
    print(f"🎬 Obteniendo detalles de {len(video_ids)} videos...")
    info = get_video_details(youtube, video_ids, start_date_dt)

    print("🎬 Obteniendo información del canal...")
    channel_info = get_channel_info(youtube)

    print("⚙️  Procesando métricas...")
    videos = process_videos(rows, info)

    print("📈 Obteniendo reportes diarios...")
    daily_rows = get_daily_reports(analytics, START_DATE, END_DATE)
    
    # Calcular métricas diarias
    avg_daily_min = 0
    comp_data = {"today": 0, "yesterday": 0, "diff_pct": 0, "trend": "flat"}
    
    if daily_rows:
        total_min = sum(row[1] for row in daily_rows)
        avg_daily_min = total_min / len(daily_rows)
        
        # Comparación (últimos 2 días con datos)
        if len(daily_rows) >= 2:
            yest_min = daily_rows[-2][1]
            today_min = daily_rows[-1][1]
            diff = today_min - yest_min
            diff_pct = (diff / yest_min * 100) if yest_min > 0 else 0
            comp_data = {
                "today": round(today_min / 60, 2),
                "yesterday": round(yest_min / 60, 2),
                "diff_pct": round(diff_pct, 1),
                "trend": "up" if diff > 0 else ("down" if diff < 0 else "flat")
            }
        elif len(daily_rows) == 1:
            comp_data["today"] = round(daily_rows[-1][1] / 60, 2)

    daily_metrics = {
        "avg_daily_hours": round(avg_daily_min / 60, 2),
        "comparison": comp_data
    }

    print("🖥️  Generando dashboard HTML...")
    html = generate_html(videos, MONETIZATION_GOAL, channel_info, daily_metrics)

    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\\n✅ Dashboard generado correctamente:")
    print(f"   {html_path}")
    
    try:
        # Solo abrir navegador si NO estamos en GitHub Actions
        if not os.getenv("GITHUB_ACTIONS"):
            print("🌐 Abriendo el dashboard en tu navegador web...")
            webbrowser.open(f"file://{html_path}")
    except Exception as e:
        print(f"No se pudo abrir automáticamente el navegador: {e}")


if __name__ == "__main__":
    main()