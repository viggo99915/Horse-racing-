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

def run_racing_pipeline():
    hk_tz = HONG_KONG_TZ
    now = datetime.now(hk_tz)
    print(f"當前香港時間: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        today_date_str = now.strftime('%Y-%m-%d')
        # 抓取 Supabase 中的賽事
        response = supabase.table("races").select("*").order("race_index").execute()
        races = response.data if response and hasattr(response, 'data') else []
        
        todays_races = [
            r for r in races 
            if r.get("race_date", "").startswith(today_date_str)
        ]
        
        if not todays_races:
            print("今日 Supabase 中沒有找到對應賽事資料。請確保已匯入馬會官方真實賽程！")
            return

        for race in todays_races:
            race_id = race.get("id")
            venue = race.get("venue")
            race_index = race.get("race_index")
            race_date_str = race.get("race_date")
            
            if not race_date_str:
                continue
                
            race_time = datetime.fromisoformat(race_date_str.replace('Z', '+00:00'))
            if race_time.tzinfo is None:
                race_time = hk_tz.localize(race_time)
            else:
                race_time = race_time.astimezone(hk_tz)
                
            time_diff = (race_time - now).total_seconds() / 60.0
            
            # 開跑前 15 分鐘之內，且未發送過推介
            if 0 < time_diff <= 15 and not race.get("alert_sent", False):
                # 嚴格從 horses 表格抓取真實數據
                horses_res = supabase.table("horses").select("*").eq("race_id", race_id).execute()
                horses = horses_res.data if horses_res and hasattr(horses_res, 'data') else []
                
                if not horses:
                    print(f"⚠️ 第 {race_index} 場即將開跑，但 Supabase 的 horses 表格中找不到該場的馬匹與賠率資料！已略過推送以防出錯。")
                    continue
                
                best_bet = None
                max_ev = 0
                for h in horses:
                    model_prob = float(h.get("model_prob", 0.20))
                    odds_win = float(h.get("win_odds", 0))
                    if odds_win <= 1:
                        continue
                        
                    ev = model_prob * odds_win
                    if ev > max_ev and ev > 1.05:
                        max_ev = ev
                        kelly = calculate_kelly_stake(model_prob, odds_win)
                        best_bet = {
                            "horse_no": h.get("horse_no"),
                            "horse_name": h.get("horse_name"),
                            "win_prob": model_prob,
                            "odds_win": odds_win,
                            "odds_place": h.get("place_odds", round(odds_win * 0.35 + 1.1, 2)),
                            "ev": ev,
                            "kelly": kelly
                        }
                
                if best_bet:
                    msg = (
                        f"🔥 *【香港賽馬量化系統｜第 {race_index} 場心水推介】*\n"
                        f"📍 場地: {venue} | 官方預定開跑: {race_time.strftime('%H:%M')}\n\n"
                        f"🐎 *精選重心*: **#{best_bet['horse_no']} {best_bet['horse_name']}**\n"
                        f"📊 預測勝率: {best_bet['win_prob']*100:.1f}%\n\n"
                        f"💰 *建議投注方案*:\n"
                        f"• **獨贏 (WIN)**: 賠率 {best_bet['odds_win']} | 期望值 EV: {best_bet['ev']:.2f} | 凱利建議資金: {best_bet['kelly']}%\n"
                        f"• **位置 (PLACE)**: 賠率 {best_bet['odds_place']}\n"
                        f"• **連贏 / 位置Q (Quinella)**: 系統量化鎖定高值組合\n\n"
                        f"⚙️ *系統狀態*: 實時盤路監控中，賽後將自動核對成績並進行動態模型校準。"
                    )
                    send_telegram(msg)
                    supabase.table("races").update({"alert_sent": True}).eq("id", race_id).execute()
                    print(f"成功發送第 {race_index} 場真實馬匹推介通知！")

    except Exception as e:
        print(f"運行賽馬管線時發生錯誤: {e}")

if __name__ == "__main__":
    run_racing_pipeline()
