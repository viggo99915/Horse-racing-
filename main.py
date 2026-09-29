import os
import requests
from datetime import datetime, timedelta
import pytz
from supabase import create_client

# ---- 1. 環境變數初始化 ----
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
HONG_KONG_TZ = pytz.timezone('Asia/Hong_Kong')

def send_telegram(message: str):
    """發送 Telegram 訊息"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

def calculate_kelly_stake(win_prob: float, odds: float) -> float:
    """凱利公式計算建議注碼百分比"""
    b = odds - 1
    p = win_prob
    q = 1 - p
    if b <= 0:
        return 0.0
    kelly = (b * p - q) / b
    return max(0.0, round(kelly * 100, 2))

def check_and_alert_upcoming_races():
    """檢查 5 分鐘後開跑的賽事並推播"""
    now = datetime.now(HONG_KONG_TZ)
    target_start = now + timedelta(minutes=4)
    target_end = now + timedelta(minutes=6)
    
    # 查詢 5 分鐘後開跑且未發送通知的賽事
    response = supabase.table("races") \
        .select("*") \
        .gte("race_date", target_start.isoformat()) \
        .lte("race_date", target_end.isoformat()) \
        .eq("alert_sent", False) \
        .execute()
    
    races = response.data
    if not races:
        print("目前沒有 5 分鐘後開跑的賽事。")
        return

    for race in races:
        race_id = race["id"]
        venue = race["venue"]
        race_index = race["race_index"]
        
        # 查詢該場次馬匹
        horses_res = supabase.table("horses").select("*").eq("race_id", race_id).execute()
        horses = horses_res.data
        
        best_bet = None
        max_ev = 0
        
        for h in horses:
            model_prob = 0.20 # 示例預測勝率
            odds = float(h["win_odds"])
            ev = model_prob * odds
            
            if ev > max_ev and ev > 1.05:
                max_ev = ev
                kelly = calculate_kelly_stake(model_prob, odds)
                best_bet = {
                    "horse_no": h["horse_number"],
                    "horse_name": h["horse_name"],
                    "win_prob": model_prob * 100,
                    "odds": odds,
                    "ev": ev,
                    "kelly": kelly
                }
        
        if best_bet:
            msg = f"⏳ *【5分鐘倒數・高價值心水】* ⏳\n\n"
            msg += f"📍 *{venue}* 第 *{race_index}* 場\n"
            msg += f"🔥 推介：*{best_bet['horse_no']}號 {best_bet['horse_name']}*\n"
            msg += f"📊 預測勝率：`{best_bet['win_prob']:.1f}%`\n"
            msg += f"💰 即時賠率：`{best_bet['odds']}`\n"
            msg += f"🚀 期望值 (EV)：`{best_bet['ev']:.2f}`\n"
            msg += f"💡 建議注碼：`{best_bet['kelly']}% 資金`\n"
            send_telegram(msg)
            
        # 標記為已發送
        supabase.table("races").update({"alert_sent": True}).eq("id", race_id).execute()

if __name__ == "__main__":
    check_and_alert_upcoming_races()
