import requests
from datetime import datetime

def get_weather():
    lat, lon = 24.3745, 88.6042
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,weather_code,rain&hourly=temperature_2m,precipitation_probability,weather_code&timezone=auto"
    aq_url = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&current=us_aqi"

    try:
        res = requests.get(url, timeout=5).json()
        aq_res = requests.get(aq_url, timeout=5).json()

        current = res.get("current", {})
        temp = current.get("temperature_2m")
        humidity = current.get("relative_humidity_2m")
        code = current.get("weather_code", 0)
        rain = current.get("rain", 0.0)
        aqi = aq_res.get("current", {}).get("us_aqi", "N/A")

        wmo_codes = {
            0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
            45: "Foggy", 51: "Light drizzle", 61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain"
        }
        condition = wmo_codes.get(code, "Clear")

        print(f"Current: {temp}C, {condition}, Rain: {rain}mm, AQI: {aqi}")
        
        hourly = res.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        probs = hourly.get("precipitation_probability", [])
        
        print("Next 6 hours:")
        now_str = datetime.now().strftime("%Y-%m-%dT%H:00")
        start_idx = 0
        for i, t in enumerate(times):
            if t >= now_str:
                start_idx = i
                break
                
        for i in range(start_idx, min(start_idx + 6, len(times))):
            t_clean = times[i].split("T")[1]
            print(f"  {t_clean} -> {temps[i]}C, Rain Prob: {probs[i]}%")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    get_weather()
