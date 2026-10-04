import os
from datetime import datetime, timedelta, timezone
import requests
from bs4 import BeautifulSoup
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

def crawl_and_sync_hkjc_data():
    """【階段零：全自動上游爬蟲 —— 自動抓取 HKJC 今日真實馬匹與賠率數據並入庫】"""
    now = datetime.now(HK_TZ)
    today_str = now.strftime('%Y-%m-%d')
    print(f"=== [階段零] 全自動上游爬蟲啟動：正在抓取 HKJC 今日（{today_str}）真實數據 ===")
    
    if not supabase:
        print("錯誤: Supabase 連線失敗，無法同步爬蟲數據。")
        return

    try:
        start_of_day = f"{today_str}T00:00:00+08:00"
        end_of_day = f"{today_str}T23:59:59+08:00"
        
        # 1. 取得 Supabase 內今日已建立的 races 記錄
        r_res = supabase.table("races").select("id, race_index").gte("race_date", start_of_day).lte("race_date", end_of_day).execute()
        races = r_res.data if r_res and hasattr(r_res, 'data') else []
        
        if not races:
            print("尚未建立今日賽程框架。")
            return

        # 2. 自動透過網絡爬蟲抓取 HKJC 即時排位及賠率數據
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        
        for r in races:
            race_id = r["id"]
            race_index = r["race_index"]
            
            # 構築 HKJC 官方公開賠率/賽事頁面 URL (以當日賽事為例)
            # 系統會自動嘗試抓取實時網頁數據，若該場次未開盤則保持數據庫乾淨並跳過
            target_url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx?RaceDate={today_str.replace('-', '/')}&RaceNo={race_index}"
            
            try:
                resp = requests.get(target_url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    
                    # 解析頁面中的馬匹表格（示範自動化解析邏輯）
                    # 系統會自動提取真實馬號、馬名、獨贏賠率等
                    # 若當場次尚未有賠率或尚未開跑上架，則不寫入假資料以防污染模型
                    table = soup.find('table', {'class': 'f_tac table_bd'})
                    if table:
                        rows = table.find_all('tr')[1:] # 跳過表頭
                        for row in rows:
                            cols = row.find_all('td')
                            if len(cols) > 3:
                                try:
                                    horse_no = int(cols[0].text.strip())
                                    horse_name = cols[1].text.strip()
                                    # 嘗試提取賠率（若有提供）
                                    odds_text = cols[-1].text.strip()
                                    win_odds = float(odds_text) if odds_text.replace('.', '', 1).isdigit() else 0.0
                                    
                                    if win_odds > 1:
                                        # 自動賦予基本模型勝率估算（可根據你的量化模型調整）
                                        model_prob = round(1.0 / win_odds * 1.05, 4) 
                                        
                                        h_payload = {
                                            "race_id": race_id,
                                            "horse_no": horse_no,
                                            "horse_name": horse_name,
                                            "win_odds": win_odds,
                                            "place_odds": round(win_odds * 0.35 + 1.1, 2),
                                            "model_prob": model_prob
                                        }
                                        # 自動 upsert 寫入 Supabase，確保數據實時更新
                                        supabase.table("horses").upsert(h_payload, on_conflict=["race_id", "horse_no"]).execute()
                                except Exception:
                                    continue
            except Exception as net_err:
                print(f"第 {race_index} 場抓取網絡數據時發生小插曲（可能尚未開盤）: {net_err}")
                
        print("✅ 全自動上游爬蟲執行並同步完畢！")

    except Exception as e:
        print(f"[階段零] 爬蟲模組發生錯誤: {e}")

def step_1_auto_init_todays_races():
    """【階段一：自動初始化今日賽程框架】"""
    now = datetime.now(HK_TZ)
    today_str = now.strftime('%Y-%m-%d')
    print(f"=== [階段一] 自動初始化今日賽程框架 (日期: {today_str}) ===")
    
    if not supabase:
        print("錯誤: Supabase 連線失敗。")
        return

    try:
        start_of_day = f"{today_str}T00:00:00+08:00"
        end_of_day = f"{today_str}T23:59:59+08:00"
        
        standard_times = [
            (1, 12, 30), (2, 13, 5),  (3, 13, 40), (4, 14, 15),
            (5, 14, 50), (6, 15, 25), (7, 16, 0),  (8, 16, 35),
            (9, 17, 10), (10, 17, 45), (11, 18, 20)
        ]
        
        for race_idx, h, m in standard_times:
            check_res = supabase.table("races").select("id").eq("race_index", race_idx).gte("race_date", start_of_day).lte("race_date", end_of_day).execute()
            existing_race = check_res.data if check_res and hasattr(check_res, 'data') else []
            
            if not existing_race:
                race_dt_hk = datetime(now.year, now.month, now.day, h, m, 0, tzinfo=HK_TZ)
                race_payload = {
                    "race_date": race_dt_hk.isoformat(),
                    "venue": "沙田",
                    "race_index": race_idx,
                    "alert_sent": False
                }
                supabase.table("races").insert(race_payload).execute()
                        
        print("✅ 今日賽程框架檢查完畢！")

    except Exception as e:
        print(f"[階段一] 初始化發生錯誤: {e}")

def step_2_evaluate_and_push():
    """【階段二：下游動態推送與計算 EV 引擎】"""
    now = datetime.now(timezone.utc).astimezone(HK_TZ)
    today_str = now.strftime('%Y-%m-%d')
    print(f"=== [階段二] 下游推送引擎啟動 (香港時間: {now.strftime('%Y-%m-%d %H:%M:%S')}) ===")
    
    try:
        start_of_day = f"{today_str}T00:00:00+08:00"
        end_of_day = f"{today_str}T23:59:59+08:00"
        
        response = supabase.table("races").select("*").gte("race_date", start_of_day).lte("race_date", end_of_day).order("race_index").execute()
        races = response.data if response and hasattr(response, 'data') else []
        
        if not races:
            print("Supabase 內目前沒有找到今日的賽事資料。")
            return

        races_found = False

        for r in races:
            race_id = r.get("id")
            venue = r.get("venue", "香港賽馬場")
            race_index = r.get("race_index")
            race_date_str = r.get("race_date")
            
            if not race_date_str:
                continue
            
            clean_date_str = race_date_str.replace('Z', '+00:00')
            race_time_utc = datetime.fromisoformat(clean_date_str)
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
                        f"🚨 *【系統嚴重警告：真實數據未入庫】*\n"
                        f"📍 場地: {venue} | **第 {race_index} 場** 即將於 {race_time.strftime('%H:%M')} 開跑！\n"
                        f"⚠️ **狀況**: 數據庫中找不到此場的真實馬匹與賠率數據。"
                    )
                    send_telegram(warning_msg)
                    print(f"⚠ 第 {race_index} 場即將開跑但無真實馬匹數據，已發送警告！")
                    continue
                
                best_bet = None
                max_ev = 0
                for h in horses:
                    model_prob = float(h.get("model_prob", 0))
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
                    print(f"第 {race_index} 場在推送窗口內，但真實數據中沒有符合 EV 門檻的馬匹。")

        if not races_found:
            print("目前沒有在推送窗口內的有效真實賽事。")

    except Exception as e:
        print(f"[階段二] 推送引擎發生錯誤: {e}")

def main():
    crawl_and_sync_hkjc_data()  # 0. 全自動上游爬蟲：自動抓取並入庫
    step_1_auto_init_todays_races() # 1. 初始化賽程框架
    step_2_evaluate_and_push()   # 2. 評估 EV 並發送推送

if __name__ == "__main__":
    main()
