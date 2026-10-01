from datetime import datetime
import pytz
# 假設你的專案中原本就有引用 supabase、telegram 等套件與相關函數

def check_and_send_notifications():
    # 設定香港時區
    hk_tz = pytz.timezone('Asia/Hong_Kong')
    now = datetime.now(hk_tz)
    
    print(f"當前香港時間: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 從資料庫（Supabase）或其他來源獲取今日賽事清單
    # races = fetch_todays_races_from_supabase()
    
    races_found = False
    
    # 假設 races 裡面包含每場比賽的開跑時間 datetime 物件 (已轉為香港時區)
    # for race in races:
    #     start_time = race['start_time'] # 需確保帶有時區或轉為 hk_tz
    #     
    #     # 計算距離開跑還有多少分鐘
    #     time_diff = (start_time - now).total_seconds() / 60.0
    #     
    #     # 檢查是否剛好在 0 到 5 分鐘之內開跑
    #     if 0 <= time_diff <= 5:
    #         print(f"賽事 {race['name']} 將於 {time_diff:.1f} 分鐘後開跑，準備發送通知...")
    #         # 執行發送 Telegram 推播的函數
    #         # send_telegram_message(race)
    #         races_found = True

    if not races_found:
        print("目前沒有 5 分鐘後開跑的賽事。")

if __name__ == "__main__":
    check_and_send_notifications()
