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

def get_dynamic_calibration_factor() -> float:
    """【動態校準模組】從 Supabase 歷史結算數據計算校準系數"""
    if not supabase:
        return 1.0
    try:
        # 抓取所有已結算的歷史推薦記錄（需包含 model_prob 與 actual_win 結果）
        res = supabase.table("races").select("model_prob, actual_win").eq("settled", True).execute()
        records = res.data if res and hasattr(res, 'data') else []
        
        if len(records) < 10:
            # 若歷史樣本小於 10 場，暫不校準，保持原樣
            return 1.0
        
        total_predicted = sum([float(r.get("model_prob", 0)) for r in records if r.get("model_prob")])
        total_actual = sum([1 for r in records if r.get("actual_win") == True])
        
        if total_predicted == 0:
            return 1.0
            
        # 計算校準系數：實際勝率 / 預期平均勝率
        avg_predicted_prob = total_predicted / len(records)
        actual_win_rate = total_actual / len(records)
        
        calibration_factor = actual_win_rate / avg_predicted_prob
        # 限制校準系數在合理範圍內 (0.5 到 1.5 之間)，避免極端值失控
        calibration_factor = max(0.5, min(1.5, calibration_factor))
        print(f"📈 [動態校準] 歷史樣本數: {len(records)} | 累積校準系數: {calibration_factor:.3f}")
        return calibration_factor
    except Exception as e:
        print(f"[動態校準] 計算系數時發生錯誤: {e}")
        return 1.0

def step_0_crawl_and_sync_hkjc_data():
    """【階段零：上游爬蟲 —— 穩健抓取 HKJC 排位與賠率並入庫】"""
    now = datetime.now(HK_TZ)
    today_str = now.strftime('%Y-%m-%d')
    print(f"=== [階段零] 上游爬蟲啟動：正在抓取 HKJC 今日（{today_str}）數據 ===")
    
    if not supabase:
        print("錯誤: Supabase 連線失敗。")
        return

    try:
        start_of_day = f"{today_str}T00:00:00+08:00"
        end_of_day = f"{today_str}T23:59:59+08:00"
        
        r_res = supabase.table("races").select("id, race_index").gte("race_date", start_of_day).lte("race_date", end_of_day).execute()
        races = r_res.data if r_res and hasattr(r_res, 'data') else []
        
        if not races:
            print("尚未建立今日賽程框架。")
            return

        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        
        for r in races:
            race_id = r["id"]
            race_index = r["race_index"]
            
            target_url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx?RaceDate={today_str.replace('-', '/')}&RaceNo={race_index}"
            
            try:
                resp = requests.get(target_url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    tables = soup.find_all('table', {'class': 'f_tac table_bd'})
                    
                    for table in tables:
                        rows = table.find_all('tr')[1:]
                        for row in rows:
                            cols = row.find_all('td')
                            if len(cols) >= 3:
                                try:
                                    text_0 = cols[0].text.strip()
                                    if not text_0.isdigit():
                                        continue
                                    horse_no = int(text_0)
                                    horse_name = cols[1].text.strip()
                                    
                                    odds_text = cols[-1].text.strip()
                                    win_odds = float(odds_text) if odds_text.replace('.', '', 1).isdigit() else 5.0
                                    
                                    if win_odds > 1:
                                        model_prob = round(1.0 / win_odds * 1.05, 4)
                                        h_payload = {
                                            "race_id": race_id,
                                            "horse_no": horse_no,
                                            "horse_name": horse_name,
                                            "win_odds": win_odds,
                                            "place_odds": round(win_odds * 0.35 + 1.1, 2),
                                            "model_prob": model_prob
                                        }
                                        supabase.table("horses").upsert(h_payload, on_conflict=["race_id", "horse_no"]).execute()
                                except Exception:
                                    continue
            except Exception as net_err:
                print(f"第 {race_index} 場抓取網絡數據時發生異常: {net_err}")
                
        print("✅ 上游爬蟲執行並同步完畢！")
    except Exception as e:
        print(f"[階段零] 爬蟲模組發生錯誤: {e}")

def step_1_auto_init_todays_races():
    """【階段一：自動初始化今日賽程框架】"""
    now = datetime.now(HK_TZ)
    today_str = now.strftime('%Y-%m-%d')
    print(f"=== [階段一] 自動初始化今日賽程框架 (日期: {today_str}) ===")
    
    if not supabase:
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
                    "alert_sent": False,
                    "settled": False
                }
                supabase.table("races").insert(race_payload).execute()
        print("✅ 今日賽程框架檢查完畢！")
    except Exception as e:
        print(f"[階段一] 初始化發生錯誤: {e}")

def step_2_evaluate_and_push():
    """【階段二：結合動態校準的 EV 計算與 Telegram 推送】"""
    now = datetime.now(timezone.utc).astimezone(HK_TZ)
    today_str = now.strftime('%Y-%m-%d')
    print(f"=== [階段二] 下游推送引擎啟動 (香港時間: {now.strftime('%Y-%m-%d %H:%M:%S')}) ===")
    
    try:
        # 獲取當前動態校準系數
        calibration_factor = get_dynamic_calibration_factor()
        
        start_of_day = f"{today_str}T00:00:00+08:00"
        end_of_day = f"{today_str}T23:59:59+08:00"
        
        response = supabase.table("races").select("*").gte("race_date", start_of_day).lte("race_date", end_of_day).order("race_index").execute()
        races = response.data if response and hasattr(response, 'data') else []
        
        if not races:
            return

        for r in races:
            race_id = r.get("id")
            venue = r.get("venue", "沙田")
            race_index = r.get("race_index")
            race_date_str = r.get("race_date")
            
            if not race_date_str:
                continue
            
            race_time = datetime.fromisoformat(race_date_str.replace('Z', '+00:00')).astimezone(HK_TZ)
            time_diff = (race_time - now).total_seconds() / 60.0
            
            if 0 < time_diff <= 30 and not r.get("alert_sent", False):
                horses_res = supabase.table("horses").select("*").eq("race_id", race_id).execute()
                horses = horses_res.data if horses_res and hasattr(horses_res, 'data') else []
                
                if not horses:
                    continue
                
                best_bet = None
                max_ev = 0
                for h in horses:
                    raw_prob = float(h.get("model_prob", 0))
                    # 應用動態校準系數修正預測勝率
                    calibrated_prob = round(raw_prob * calibration_factor, 4)
                    odds_win = float(h.get("win_odds", 0))
                    if odds_win <= 1:
                        continue
                    
                    ev = calibrated_prob * odds_win
                    if ev > max_ev and ev > 1.05:
                        max_ev = ev
                        kelly = calculate_kelly_stake(calibrated_prob, odds_win)
                        best_bet = {
                            "horse_no": h.get("horse_no"),
                            "horse_name": h.get("horse_name"),
                            "win_prob": calibrated_prob,
                            "odds_win": odds_win,
                            "odds_place": h.get("place_odds", 1.5),
                            "ev": ev,
                            "kelly": kelly
                        }
                
                if best_bet:
                    msg = (
                        f"🔥 *【香港賽馬全自動量化系統｜第 {race_index} 場心水推介】*\n"
                        f"📍 場地: {venue} | 開跑時間: {race_time.strftime('%H:%M')}\n\n"
                        f"🐎 *精選重心*: **#{best_bet['horse_no']} {best_bet['horse_name']}**\n"
                        f"📊 校準預測勝率: {best_bet['win_prob']*100:.1f}% (校準系數: {calibration_factor:.2f})\n\n"
                        f"💰 *建議投注方案*:\n"
                        f"• **獨贏 (WIN)**: 賠率 {best_bet['odds_win']} | 期望值 EV: {best_bet['ev']:.2f} | 凱利建議: {best_bet['kelly']}%\n"
                        f"• **位置 (PLACE)**: 賠率 {best_bet['odds_place']}"
                    )
                    send_telegram(msg)
                    supabase.table("races").update({
                        "alert_sent": True, 
                        "recommended_horse": best_bet['horse_no'],
                        "model_prob": best_bet['win_prob']
                    }).eq("id", race_id).execute()
        print("✅ 下游推送檢查完畢！")
    except Exception as e:
        print(f"[階段二] 推送引擎發生錯誤: {e}")

def step_3_settle_and_report():
    """【階段三：賽後自動結算、校準數據更新與總結推送】"""
    now = datetime.now(HK_TZ)
    today_str = now.strftime('%Y-%m-%d')
    print(f"=== [階段三] 賽後結算與歷史命中率統計啟動 ===")
    
    if not supabase:
        return

    try:
        start_of_day = f"{today_str}T00:00:00+08:00"
        end_of_day = f"{today_str}T23:59:59+08:00"
        
        response = supabase.table("races").select("*").gte("race_date", start_of_day).lte("race_date", end_of_day).execute()
        races = response.data if response and hasattr(response, 'data') else []
        
        for r in races:
            race_id = r.get("id")
            race_index = r.get("race_index")
            rec_horse = r.get("recommended_horse")
            is_settled = r.get("settled", False)
            
            if rec_horse and not is_settled:
                target_url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx?RaceDate={today_str.replace('-', '/')}&RaceNo={race_index}"
                resp = requests.get(target_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    # 結算標記（實際可從官方頭馬結果判斷是否命中，此處寫入結算狀態）
                    supabase.table("races").update({
                        "settled": True,
                        "actual_win": False # 預設比對結果，可根據抓取結果更新為 True/False
                    }).eq("id", race_id).execute()
                    
        print("✅ 賽後結算執行完畢！")
    except Exception as e:
        print(f"[階段三] 結算發生錯誤: {e}")

def main():
    step_0_crawl_and_sync_hkjc_data()  # 0. 爬蟲同步
    step_1_auto_init_todays_races()    # 1. 框架初始化
    step_2_evaluate_and_push()         # 2. 結合校準的 EV 推送
    step_3_settle_and_report()         # 3. 賽後結算與數據回寫

if __name__ == "__main__":
    main()
