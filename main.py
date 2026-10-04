import os
from datetime import datetime, timedelta, timezone
import requests
from supabase import create_client

# ----------------- 1. 環境變數初始化 -----------------
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None
HK_TZ = timezone(timedelta(hours=8))

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

def run_main_bot():
    """下游動態推送與計算 EV 引擎（完全依賴真實上游數據）"""
    now = datetime.now(timezone.utc).astimezone(HK_TZ)
    print(f"=== 賽馬量化推送引擎啟動 (香港時間: {now.strftime('%Y-%m-%d %H:%M:%S')}) ===")
    
    try:
        # 動態從 Supabase 讀取所有賽事資料
        response = supabase.table("races").select("*").order("race_index").execute()
        races = response.data if response and hasattr(response, 'data') else []
        
        if not races:
            print("Supabase 內目前沒有找到任何賽事資料（等待上游爬蟲入庫）。")
            return

        races_found = False

        for r in races:
            race_id = r.get("id")
            venue = r.get("venue", "香港賽馬場")
            race_index = r.get("race_index")
            race_date_str = r.get("race_date")
            if not race_date_str:
                continue
            
            # 🛠️ 智慧時區校準：直接強制解析並對齊香港時間 (HKT)
            # 如果字串本身帶有 +00:00 但其實係本地時間，直接轉成帶 +08:00
            clean_date_str = race_date_str.replace('Z', '')
            if '+' not in clean_date_str and '-' not in clean_date_str[10:]:
                # 如果沒有時區標記，直接補上 +08:00
                race_time = datetime.fromisoformat(clean_date_str).replace(tzinfo=HK_TZ)
            else:
                # 如果有時區標記，安全轉換
                dt_obj = datetime.fromisoformat(clean_date_str)
                if dt_obj.tzinfo is None:
                    race_time = dt_obj.replace(tzinfo=HK_TZ)
                else:
                    # 如果上游錯手存成了 UTC，我們直接修正其時區偏移或直接取其數字
                    # 這裡直接強制轉成香港時間顯示
                    race_time = dt_obj.astimezone(HK_TZ)

            # 將資料庫時間轉換成香港時間
            race_time_utc = datetime.fromisoformat(race_date_str.replace('Z', '+00:00'))
            race_time = race_time_utc.astimezone(HK_TZ)
            
            time_diff = (race_time - now).total_seconds() / 60.0
            print(f"-> 第 {race_index} 場 | 開跑時間(HK): {race_time.strftime('%H:%M')} | 距離開跑: {time_diff:.1f} 分鐘 | 已發送: {r.get('alert_sent', False)}")
            
            if time_diff <= 0:
                continue
                
            # 🛡️ 【30分鐘預警與推送窗口】
            if 0 < time_diff <= 30 and not r.get("alert_sent", False):
                races_found = True
                
                horses_res = supabase.table("horses").select("*").eq("race_id", race_id).execute()
                horses = horses_res.data if horses_res and hasattr(horses_res, 'data') else []
                
                if not horses:
                    warning_msg = (
                        f"🚨 *【系統嚴重警告：上游數據未入庫】*\n"
                        f"📍 場地: {venue} | **第 {race_index} 場** 即將於 {race_time.strftime('%H:%M')} 開跑！\n"
                        f"⚠️ **狀況**: Supabase 內找不到此場的真實馬匹數據。"
                    )
                    send_telegram(warning_msg)
                    print(f"⚠ 第 {race_index} 場即將開跑但無馬匹數據，已發送警告！")
                    continue
                
                best_bet = None
                max_ev = 0
                for h in horses:
                    model_prob = float(h.get("model_prob", 0.25))
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
                        f"🔥 *【香港賽馬全自動量化系統｜第 {race_index} 場心水推介】*\n"
                        f"📍 場地: {venue} | 官方預定開跑: {race_time.strftime('%H:%M')}\n\n"
                        f"🐎 *精選重心*: **#{best_bet['horse_no']} {best_bet['horse_name']}**\n"
                        f"📊 預測勝率: {best_bet['win_prob']*100:.1f}%\n\n"
                        f"💰 *建議投注方案*:\n"
                        f"• **獨贏 (WIN)**: 賠率 {best_bet['odds_win']} | 期望值 EV: {best_bet['ev']:.2f} | 凱利建議資金: {best_bet['kelly']}%\n"
                        f"• **位置 (PLACE)**: 賠率 {best_bet['odds_place']}"
                    )
                    send_telegram(msg)
                    supabase.table("races").update({"alert_sent": True}).eq("id", race_id).execute()
                    print(f"成功發送第 {race_index} 場推介通知！")
                else:
                    print(f"第 {race_index} 場在推送窗口內，但沒有符合 EV 門檻的馬匹。")

        if not races_found:
            print("目前沒有在推送窗口內的有效真實賽事。")

    except Exception as e:
        print(f"運行賽馬程式時發生錯誤: {e}")

if __name__ == "__main__":
    run_main_bot()
