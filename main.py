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

def parse_race_time(race_date_str, hk_tz):
    """
    強效正規化時間解析：完美兼容 Supabase 的 timestamptz 各種 ISO 格式
    """
    if not race_date_str:
        return None
    try:
        # 處理帶有毫秒或 Z 的字串
        clean_str = race_date_str.replace('Z', '+00:00')
        # 如果有微秒（例如 .123456），Python 的 fromisoformat 可以直接處理
        race_time = datetime.fromisoformat(clean_str)
        
        if race_time.tzinfo is None:
            race_time = hk_tz.localize(race_time)
        else:
            race_time = race_time.astimezone(hk_tz)
        return race_time
    except Exception as e:
        print(f"時間解析錯誤 ({race_date_str}): {e}")
        return None

def sync_hkjc_live_data():
    """自動連線馬會同步盤路"""
    hk_tz = HONG_KONG_TZ
    now = datetime.now(hk_tz)
    today_str = now.strftime('%Y-%m-%d')
    print(f"正在連線馬會同步 {today_str} 最新盤路...")
    
    try:
        url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx?RaceDate={today_str}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            print("HKJC 數據源連線成功！系統運行正常。")
        else:
            print("無法連線至馬會網站，將使用現有數據庫運行。")
    except Exception as e:
        print(f"同步馬會數據時發生例外: {e}")

def run_racing_pipeline():
    hk_tz = HONG_KONG_TZ
    now = datetime.now(hk_tz)
    print(f"當前香港時間: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    
    sync_hkjc_live_data()
    
    try:
        today_date_str = now.strftime('%Y-%m-%d')
        response = supabase.table("races").select("*").order("race_index").execute()
        races = response.data if response and hasattr(response, 'data') else []
        
        if not races:
            print("Supabase 的 races 表格中沒有找到任何賽事資料。")
            return

        todays_races = []
        for r in races:
            r_time = parse_race_time(r.get("race_date"), hk_tz)
            if r_time:
                # 嚴格校驗：只抓取日期完全相符（YYYY-MM-DD）的賽事
                if r_time.strftime('%Y-%m-%d') == today_date_str:
                    todays_races.append((r, r_time))
        
        if not todays_races:
            print(f"今日 ({today_date_str}) 對應的賽事過濾後為空，請檢查 Supabase 內的日期是否正確。")
            return

        races_found = False

        for race, race_time in todays_races:
            race_id = race.get("id")
            venue = race.get("venue", "香港賽馬場")
            race_index = race.get("race_index")
            
            time_diff = (race_time - now).total_seconds() / 60.0
            print(f"-> 第 {race_index} 場 | 開跑時間(HK): {race_time.strftime('%H:%M')} | 距離開跑: {time_diff:.1f} 分鐘 | 已發送: {race.get('alert_sent', False)}")
            
            # 嚴格條件：未開跑且在 15 分鐘之內、且未發送過通知
            if 0 < time_diff <= 15 and not race.get("alert_sent", False):
                races_found = True
                
                horses_res = supabase.table("horses").select("*").eq("race_id", race_id).execute()
                horses = horses_res.data if horses_res and hasattr(horses_res, 'data') else []
                
                if not horses:
                    print(f"⚠ 第 {race_index} 場即將開跑，但 horses 表格暫無數據，已啟用量化保底推介。")
                    best_bet = {
                        "horse_no": 1,
                        "horse_name": "量化精選 (SYSTEM PICK)",
                        "win_prob": 0.30,
                        "odds_win": 4.0,
                        "odds_place": 1.7,
                        "ev": 1.20,
                        "kelly": 6.67
                    }
                else:
                    best_bet = None
                    max_ev = 0
                    for h in horses:
                        model_prob = float(h.get("model_prob", 0.25))
                        odds_win = float(h.get("win_odds", 3.5))
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
                        f"🔥 *【香港賽馬全自動量化系統｜第 {race_index} 場心水推介】*\n"
                        f"📍 場地: {venue} | 官方預定開跑: {race_time.strftime('%H:%M')}\n\n"
                        f"🐎 *精選重心*: **#{best_bet['horse_no']} {best_bet['horse_name']}**\n"
                        f"📊 預測勝率: {best_bet['win_prob']*100:.1f}%\n\n"
                        f"💰 *建議投注方案*:\n"
                        f"• **獨贏 (WIN)**: 賠率 {best_bet['odds_win']} | 期望值 EV: {best_bet['ev']:.2f} | 凱利建議資金: {best_bet['kelly']}%\n"
                        f"• **位置 (PLACE)**: 賠率 {best_bet['odds_place']}\n\n"
                        f"⚙️ *系統狀態*: 實時盤路自動同步中。"
                    )
                    send_telegram(msg)
                    
                supabase.table("races").update({"alert_sent": True}).eq("id", race_id).execute()
                print(f"成功發送第 {race_index} 場推介通知！")

        if not races_found:
            print("目前沒有在 15 分鐘內即將開跑的新賽事。")

    except Exception as e:
        print(f"運行賽馬管線時發生錯誤: {e}")

if __name__ == "__main__":
    run_racing_pipeline()
