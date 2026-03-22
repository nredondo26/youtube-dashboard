import os
import json
import webbrowser
from datetime import datetime, timezone
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
import isodate
from dotenv import load_dotenv
from typing import List, Dict, Any

# Cargar variables de entorno
load_dotenv()

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────
API_KEY            = os.getenv("API_KEY", "")
CHANNEL_ID         = os.getenv("CHANNEL_ID", "UCiFazFymzsTLHTA2dvEsa8A")
CLIENT_SECRET_FILE = os.getenv("CLIENT_SECRET_FILE", "client_secret.json")
SCOPES             = ["https://www.googleapis.com/auth/yt-analytics.readonly"]

START_DATE         = datetime.now(timezone.utc).date().replace(month=1, day=1).strftime("%Y-%m-%d")
END_DATE           = datetime.now(timezone.utc).date().strftime("%Y-%m-%d")
MONETIZATION_GOAL  = 4000          # horas requeridas para monetizar
MIN_DURATION_SEC   = 120           # Solo videos > 2 minutos (para monetización)
MAX_RESULTS        = 200           # El límite de la API para la dimensión 'video' es 200


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
        part="snippet,statistics,contentDetails",
        id=CHANNEL_ID
    ).execute()

    if not res.get("items"):
        return {"title": "Canal de YouTube", "thumb": "", "subs": 0, "uploads_id": ""}

    item = res["items"][0]
    return {
        "title": item["snippet"]["title"],
        "thumb": item["snippet"]["thumbnails"].get("high", item["snippet"]["thumbnails"].get("default", {}))["url"],
        "subs":  int(item["statistics"].get("subscriberCount", 0)),
        "uploads_id": item["contentDetails"]["relatedPlaylists"]["uploads"]
    }

def get_all_uploads(youtube, uploads_playlist_id: str, start_date_str: str) -> List[str]:
    """Obtiene todos los videos de la playlist de subidas filtrados por fecha."""
    video_ids = []
    next_page_token = None
    start_dt = datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    
    while True:
        res = youtube.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=50,
            pageToken=next_page_token
        ).execute()
        
        for item in res.get("items", []):
            published_at_str = item["snippet"]["publishedAt"]
            published_at = datetime.fromisoformat(published_at_str.replace("Z", "+00:00"))
            
            if published_at < start_dt:
                return video_ids # Ya llegamos a videos más antiguos
            
            video_ids.append(item["contentDetails"]["videoId"])
            
        next_page_token = res.get("nextPageToken")
        if not next_page_token:
            break
            
    return video_ids


# ─────────────────────────────────────────────
# OBTENER DETALLES DE VIDEOS
# ─────────────────────────────────────────────
def get_video_details(youtube, video_ids: Any, start_date_dt: datetime) -> Dict[str, Any]:
    info = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        res   = youtube.videos().list(
            part="snippet,contentDetails,statistics",
            id=",".join(batch),
        ).execute()

        for item in res.get("items", []):
            vid = item["id"]
            
            # Robust extraction of fields
            content_details = item.get("contentDetails", {})
            snippet         = item.get("snippet", {})
            statistics      = item.get("statistics", {})
            
            duration_iso = content_details.get("duration")
            if not duration_iso:
                # Omitir videos sin duración (ej. transmisiones en vivo actuales o errores)
                print(f"   [Aviso] El video {vid} ('{snippet.get('title', 'Sin título')}') no tiene duración reportada. Saltando.")
                continue

            try:
                seconds = isodate.parse_duration(duration_iso).total_seconds()
            except Exception as e:
                print(f"   [Error] No se pudo parsear la duración del video {vid}: {e}")
                continue

            published_str  = snippet.get("publishedAt", "")[:10]
            if not published_str:
                continue

            if seconds >= MIN_DURATION_SEC:
                info[vid] = {
                    "title":     snippet.get("title", "Sin título"),
                    "published": published_str,
                    "duration":  round(seconds / 60, 2),
                    "likes":     int(statistics.get("likeCount", 0)),
                    "comments":  int(statistics.get("commentCount", 0))
                }
    return info


# ─────────────────────────────────────────────
# PROCESAR MÉTRICAS
# ─────────────────────────────────────────────
def process_videos(rows: List[List[Any]], info: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
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
def generate_html(videos: List[Dict[str, Any]], goal: float, channel_info: dict, daily_data: dict) -> str:
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
        html_content = f.read()

    # Reemplazar placeholders
    html_content = html_content.replace("[[START_DATE]]", START_DATE)
    html_content = html_content.replace("[[END_DATE]]", END_DATE)
    html_content = html_content.replace("[[GENERATED_AT]]", generated_at)
    html_content = html_content.replace("[[GOAL]]", str(goal))
    html_content = html_content.replace("[[GOAL_INT]]", f"{int(goal):,}")
    html_content = html_content.replace("[[JS_VIDEOS]]", js_videos)
    html_content = html_content.replace("[[CHANNEL_TITLE]]", channel_info["title"])
    html_content = html_content.replace("[[CHANNEL_THUMB]]", channel_info["thumb"])
    html_content = html_content.replace("[[CHANNEL_SUBS]]", f"{channel_info['subs']:,}")
    html_content = html_content.replace("[[JS_DAILY_METRICS]]", json.dumps(daily_data, ensure_ascii=False))

    return html_content


def export_summary(videos: List[Dict[str, Any]], goal: float, channel_info: dict, daily_data: dict):
    """Genera un archivo JSON ligero con las métricas clave para la integración móvil."""
    total_hours = sum(v["hours"] for v in videos)
    goal_pct = round((total_hours / goal) * 100, 1) if goal > 0 else 0
    
    summary = {
        "title": channel_info["title"],
        "thumb": channel_info["thumb"],
        "subs": channel_info["subs"],
        "total_hours": round(total_hours, 1),
        "goal": goal,
        "goal_pct": goal_pct,
        "avg_daily_hours": daily_data["avg_daily_hours"],
        "trend": daily_data["comparison"]["trend"],
        "diff_pct": daily_data["comparison"]["diff_pct"],
        "last_update": datetime.now().isoformat()
    }
    
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "summary.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"   [Summary JSON generado]: {path}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    print("[Autenticando]...")
    analytics, youtube = authenticate()

    print("[Obteniendo informacíón del canal]...")
    channel_info = get_channel_info(youtube)

    print(f"[Listando videos subidos desde {START_DATE}]...")
    video_ids_all = get_all_uploads(youtube, channel_info["uploads_id"], START_DATE)

    start_date_dt = datetime.strptime(START_DATE, "%Y-%m-%d")
    print(f"[Analizando duración de] {len(video_ids_all)} videos...")
    info = get_video_details(youtube, video_ids_all, start_date_dt)
    
    # Lista final de IDs que cumplen el filtro de duración
    valid_video_ids = list(info.keys())

    print(f"[Obteniendo analytics] ({START_DATE} -> {END_DATE})...")
    analytics_rows = get_analytics(analytics, START_DATE, END_DATE)
    # Convertir a dict para búsqueda rápida: {video_id: [estimatedMinutes, views, subs]}
    analytics_map = {r[0]: r[1:] for r in analytics_rows}

    print("[Procesando métricas]...")
    # Preparar filas simulando el formato de analytics para procesar todos los videos válidos
    merged_rows = []
    for vid in valid_video_ids:
        stats = analytics_map.get(vid, [0, 0, 0]) # 0 si no hay datos aún
        merged_rows.append([vid] + stats)

    videos = process_videos(merged_rows, info)

    print("[Obteniendo reportes diarios]...")
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

    print("[Generando dashboard HTML]...")
    html = generate_html(videos, MONETIZATION_GOAL, channel_info, daily_metrics)
    
    print("[Exportando resumen para móvil]...")
    export_summary(videos, MONETIZATION_GOAL, channel_info, daily_metrics)

    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n[Dashboard generado correctamente]:")
    print(f"   {html_path}")
    
    try:
        # Solo abrir navegador si NO estamos en GitHub Actions
        if not os.getenv("GITHUB_ACTIONS"):
            print("[Abriendo el dashboard en tu navegador web]...")
            webbrowser.open(f"file://{html_path}")
    except Exception as e:
        print(f"No se pudo abrir automáticamente el navegador: {e}")


if __name__ == "__main__":
    main()