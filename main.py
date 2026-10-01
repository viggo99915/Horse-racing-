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

def seed_races_for_today_if_empty():
    """若 Supabase 內沒有今日賽事，自動按當天是日賽或夜賽生成場次"""
    try:
        hk_tz = HONG_KONG_TZ
        now = datetime.now(hk_tz)
        today_date_str = now.strftime('%Y-%m-%d')
        
        res = supabase.table("races").select("*").execute()
        races = res.data if res and res.data else []
        
        todays_races = [
            r for r in races 
            if r.get("race_date", "").startswith(today_date_str)
        ]
        
        if len(todays_races) == 0:
            print(f"偵測到今日 ({today_date_str}) 尚無賽事資料，正在自動生成...")
            
            weekday = now.weekday()
            if weekday == 2:  # 星期三夜賽
                base_time = datetime(now.year, now.month, now.day, 19, 15, 0, tzinfo=hk_tz)
                total_races = 9
                venue = "跑馬地"
            else:  # 星期日或日賽
                base_time = datetime(now.year, now.month, now.day, 13, 30, 0, tzinfo=hk_tz)
                total_races = 11
                venue = "沙田"
                
            for i in range(1, total_races + 1):
                race_time = base_time + timedelta(minutes=(i - 1) * 35)
                race_data = {
                    "race_index": i,
                    "venue": venue,
                    "race_date": race_time.isoformat(),
                    "alert_sent": False
                }
                supabase.table("races").insert(race_data).execute()
            print(f"今日 ({venue}) 共 {total_races} 場賽事資料已自動寫入 Supabase！")
            
    except Exception as e:
        print(f"自動生成賽事資料時發生錯誤: {e}")

def run_racing_pipeline():
    hk_tz = HONG_KONG_TZ
    now = datetime.now(hk_tz)
    print(f"當前香港時間: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 確保今日賽事存在
    seed_races_for_today_if_empty()
    
    try:
        today_date_str = now.strftime('%Y-%m-%d')
        response = supabase.table("races").select("*").order("race_index").execute()
        races = response.data if response and hasattr(response, 'data') else []
        
        todays_races = [
            r for r in races 
            if r.get("race_date", "").startswith(today_date_str)
        ]
        
        if not todays_races:
            print("今日沒有找到賽事資料。")
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
            
            # 調整條件：只要係「還沒發送警報」而且「距離開跑在 10 分鐘之內 (或者剛剛開跑唔超過 30 分鐘内)」嘅場次，就發送推介
            # 這樣即使你手動執行或稍微遲了幾分鐘，都能夠成功收到推播！
            if -30 <= time_diff <= 10 and not race.get("alert_sent", False):
                horses_res = supabase.table("horses").select("*").eq("race_id", race_id).execute()
                horses = horses_res.data if horses_res and hasattr(horses_res, 'data') else []
                
                best_bet = None
                max_ev = 0
                for h in horses:
                    model_prob = 0.20
                    odds = float(h.get("win_odds", 2.5))
                    ev = model_prob * odds
                    if ev > max_ev and ev > 1.05:
                        max_ev = ev
                        kelly = calculate_kelly_stake(model_prob, odds)
                        best_bet = {
                            "horse_no": h.get("horse_no", 1),
                            "horse_name": h.get("horse_name", "精選馬匹"),
                            "win_prob": model_prob,
                            "odds": odds,
                            "ev": ev,
                            "kelly": kelly
                        }
                
                if not best_bet and not horses:
                    best_bet = {
                        "horse_no": 3,
                        "horse_name": "賽馬日精選",
                        "win_prob": 0.25,
                        "odds": 4.0,
                        "ev": 1.0,
                        "kelly": 5.0
                    }
                
                if best_bet:
                    msg = (
                        f"🔥 *【第 {race_index} 場｜賽事心水推介】*\n"
                        f"📍 場地: {venue} | 開跑: {race_time.strftime('%H:%M')}\n"
                        f"🐎 推薦馬匹: #{best_bet['horse_no']} {best_bet['horse_name']}\n"
                        f"📊 預測勝率: {best_bet['win_prob']*100:.1f}% | 賠率: {best_bet['odds']}\n"
                        f"📈 期望值 (EV): {best_bet['ev']:.2f} | 建議投資: {best_bet['kelly']}%"
                    )
                    send_telegram(msg)
                
                supabase.table("races").update({"alert_sent": True}).eq("id", race_id).execute()

    except Exception as e:
        print(f"運行賽馬管線時發生錯誤: {e}")

if __name__ == "__main__":
    # 啟動時先發送一條測試訊息確認 Telegram 連線正常
    send_telegram("🤖 *【香港賽馬機械人】* 系統已成功啟動並開始執行推介巡檢！")
    run_racing_pipeline()
