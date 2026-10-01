import os
import requests
from datetime import datetime, timedelta
import pytz
from supabase import create_client

# ----------------- 1. 環境變數初始化 -----------------
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None
HONG_KONG_TZ = pytz.timezone('Asia/Hong_Kong')

def send_telegram(message: str):
    """發送 Telegram 訊息"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram Token 或 Chat ID 未設定，跳過發送。")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("Telegram 訊息發送成功！")
        else:
            print(f"Telegram 發送失敗: {response.text}")
    except Exception as e:
        print(f"發送 Telegram 時發生錯誤: {e}")

def calculate_kelly_stake(win_prob: float, odds: float) -> float:
    """凱利公式計算建議投注百分比"""
    b = odds - 1
    p = win_prob
    q = 1 - p
    if b <= 0:
        return 0.0
    kelly = (b * p - q) / b
    return max(0.0, round(kelly * 100, 2))

def check_and_send_notifications():
    # 檢查 5 分鐘後開跑的賽事邏輯
    hk_tz = HONG_KONG_TZ
    now = datetime.now(hk_tz)
    
    print(f"當前香港時間: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    
    target_start = now + timedelta(minutes=0)
    target_end = now + timedelta(minutes=5)
    
    races_found = False
    
    try:
        # 從 Supabase 查詢介乎 0 到 5 分鐘之內開跑且未發送過警報的賽事
        response = supabase.table("races") \
            .select("*") \
            .gte("race_date", target_start.isoformat()) \
            .lte("race_date", target_end.isoformat()) \
            .eq("alert_sent", False) \
            .execute()
            
        races = response.data if response and hasattr(response, 'data') else []
        
        for race in races:
            race_id = race.get("id")
            venue = race.get("venue")
            race_index = race.get("race_index")
            
            # 查詢該場馬匹
            horses_res = supabase.table("horses").select("*").eq("race_id", race_id).execute()
            horses = horses_res.data if horses_res and hasattr(horses_res, 'data') else []
            
            best_bet = None
            max_ev = 0
            
            for h in horses:
                model_prob = 0.20  # 示例預測機率，可根據你的模型調整
                odds = float(h.get("win_odds", 0))
                ev = model_prob * odds
                
                if ev > max_ev and ev > 1.05:
                    max_ev = ev
                    kelly = calculate_kelly_stake(model_prob, odds)
                    best_bet = {
                        "horse_no": h.get("horse_no"),
                        "horse_name": h.get("horse_name"),
                        "win_prob": model_prob,
                        "odds": odds,
                        "ev": ev,
                        "kelly": kelly
                    }
            
            if best_bet:
                msg = (
                    f"🔥 *【5分鐘後｜高價值心水】*\n"
                    f"📍 馬場/場次: {venue} 第 {race_index} 場\n"
                    f"🐎 推介馬匹: #{best_bet['horse_no']} {best_bet['horse_name']}\n"
                    f"📊 預測勝率: {best_bet['win_prob']*100:.1f}%\n"
                    f"💰 賠率 (Odds): {best_bet['odds']}\n"
                    f"📈 期望值 (EV): {best_bet['ev']:.2f}\n"
                    f"💡 建議投資: {best_bet['kelly']}%"
                )
                send_telegram(msg)
                
            # 標記為已發送，避免重複推送
            supabase.table("races").update({"alert_sent": True}).eq("id", race_id).execute()
            races_found = True

    except Exception as e:
        print(f"查詢或發送賽事通知時發生錯誤: {e}")

    if not races_found:
        print("目前沒有 5 分鐘後開跑的賽事。")

if __name__ == "__main__":
    check_and_send_notifications()
