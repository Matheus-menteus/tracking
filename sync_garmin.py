import os
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta

MEMORIA_FILE = "memoria.json"
BASE_URL = "https://personal.connectapi.garmin.com"
TOKEN_URL = f"{BASE_URL}/personalapitoken-service/personal-api/personal-api-key/token"


def obtain_access_token(personal_api_key: str) -> str:
    """Troca a Chave de API Pessoal do Garmin Connect por um Access Token OAuth2 (válido por 24h)."""
    req = urllib.request.Request(
        TOKEN_URL,
        method="POST",
        headers={
            "Authorization": f"ApiKey {personal_api_key.strip()}",
            "Accept": "application/json",
            "Content-Length": "0"
        }
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        token = data.get("access_token") or data.get("accessToken") or data.get("token")
        if not token:
            raise RuntimeError(f"Token não retornado pela API: {data}")
        return token


def api_get(path: str, access_token: str, params: dict = None):
    """Faz uma chamada GET autenticada na Garmin Connect Personal API."""
    url = f"{BASE_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        print(f"Aviso HTTP {e.code} em {path}")
        return None
    except Exception as e:
        print(f"Aviso em {path}: {e}")
        return None


def map_sport(activity_type: str) -> str:
    t = (activity_type or "").lower()
    if "run" in t or "treadmill" in t:
        return "run"
    if "cycl" in t or "bike" in t or "biking" in t or "ride" in t:
        return "bike"
    if "swim" in t:
        return "swim"
    if "multi" in t or "transition" in t or "tri" in t:
        return "tri"
    if "strength" in t or "fitness" in t or "gym" in t or "weight" in t:
        return "gym"
    return "run"


def empty_garmin_week():
    return [
        {
            "sport": "none",
            "title": "",
            "distanceKm": 0,
            "durationMin": 0,
            "avgHr": 0,
            "sleepHours": 0,
            "sleepScore": 0,
            "restingHr": 0,
            "bodyBattery": 0,
            "steps": 0
        }
        for _ in range(7)
    ]


def main():
    api_key = os.environ.get("GARMIN_PERSONAL_API_KEY", "").strip()
    if not api_key:
        raise ValueError("Configure o Secret GARMIN_PERSONAL_API_KEY nas configurações do repositório GitHub.")

    print("1. Obtendo Access Token via Garmin Personal API...")
    access_token = obtain_access_token(api_key)
    print("   Access Token obtido com sucesso!")

    # Horário de Brasília (UTC-3)
    now = datetime.utcnow() - timedelta(hours=3)
    monday = now - timedelta(days=now.weekday())
    sunday = monday + timedelta(days=6)
    monday_key = monday.strftime("%Y-%m-%d")
    sunday_key = sunday.strftime("%Y-%m-%d")
    today_key = now.strftime("%Y-%m-%d")

    # Carrega memoria.json atual
    if os.path.exists(MEMORIA_FILE):
        with open(MEMORIA_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    else:
        state = {
            "currentWeekKey": monday_key,
            "habits": [],
            "garminWeek": empty_garmin_week(),
            "history": {}
        }

    # Verifica virada automática de semana
    if state.get("currentWeekKey") and state["currentWeekKey"] < monday_key:
        prev_key = state["currentWeekKey"]
        state.setdefault("history", {})[prev_key] = {
            "weekKey": prev_key,
            "archivedAt": datetime.utcnow().isoformat() + "Z",
            "habits": json.loads(json.dumps(state.get("habits", []))),
            "garminWeek": json.loads(json.dumps(state.get("garminWeek", empty_garmin_week())))
        }
        for h in state.get("habits", []):
            h["days"] = [False] * 7
        state["garminWeek"] = empty_garmin_week()
        state["currentWeekKey"] = monday_key

    if "garminWeek" not in state or len(state["garminWeek"]) != 7:
        state["garminWeek"] = empty_garmin_week()

    # 2. Busca todas as atividades da semana em 1 única requisição
    print(f"2. Buscando atividades de {monday_key} até {sunday_key}...")
    activities = api_get(
        "/activitylist-service/activities/search/activities",
        access_token,
        {"startDate": monday_key, "endDate": sunday_key, "start": 0, "limit": 30}
    )
    act_list = activities if isinstance(activities, list) else (activities or {}).get("activityList", [])

    # 3. Busca o resumo de sono da semana inteira em 1 única requisição
    print(f"3. Buscando sono da semana ({monday_key} a {today_key})...")
    sleep_summaries = api_get(
        "/sleep-service/sleep/dailySleepSummariesByDate",
        access_token,
        {"startDate": monday_key, "endDate": today_key}
    )
    sleep_list = sleep_summaries if isinstance(sleep_summaries, list) else (sleep_summaries or {}).get("dailySleepDTOList", [])

    # 4. Busca passos da semana em 1 única requisição
    steps_range = api_get(
        f"/usersummary-service/stats/steps/daily/{monday_key}/{today_key}",
        access_token
    )
    steps_list = steps_range if isinstance(steps_range, list) else []

    # 5. Preenche cada dia de Segunda (0) até Hoje (now.weekday())
    for day_idx in range(now.weekday() + 1):
        target_date = (monday + timedelta(days=day_idx)).strftime("%Y-%m-%d")
        gm_day = state["garminWeek"][day_idx]

        # Atividades do dia
        day_acts = [
            a for a in (act_list or [])
            if str(a.get("startTimeLocal") or a.get("startTimeGMT") or "").startswith(target_date)
        ]
        if day_acts:
            main_act = day_acts[0]
            act_type = main_act.get("activityType", {})
            type_key = act_type.get("typeKey", "") if isinstance(act_type, dict) else str(act_type)
            gm_day["sport"] = "tri" if len(day_acts) > 1 else map_sport(type_key)
            gm_day["title"] = " + ".join((a.get("activityName") or "Treino Garmin") for a in day_acts)
            gm_day["distanceKm"] = round(sum((a.get("distance") or 0) for a in day_acts) / 1000.0, 2)
            gm_day["durationMin"] = round(sum((a.get("duration") or a.get("movingDuration") or 0) for a in day_acts) / 60)
            gm_day["avgHr"] = round(main_act.get("averageHR") or 0)

        # Sono do dia
        day_sleep = next(
            (s for s in (sleep_list or []) if str(s.get("calendarDate") or "").startswith(target_date)),
            None
        )
        if not day_sleep:
            single_sleep = api_get("/sleep-service/sleep/dailySleep", access_token, {"date": target_date})
            if isinstance(single_sleep, dict):
                day_sleep = single_sleep.get("dailySleepDTO") or single_sleep

        if isinstance(day_sleep, dict):
            secs = day_sleep.get("sleepTimeSeconds") or 0
            if secs > 0:
                gm_day["sleepHours"] = round(secs / 3600.0, 2)
            scores = day_sleep.get("sleepScores") or {}
            overall = (scores.get("overall") or {}).get("value") or day_sleep.get("overallSleepScore") or 0
            if overall:
                gm_day["sleepScore"] = round(overall)

        # Passos da lista semanal
        day_steps_obj = next(
            (st for st in (steps_list or []) if str(st.get("calendarDate") or "").startswith(target_date)),
            None
        )
        if isinstance(day_steps_obj, dict) and day_steps_obj.get("totalSteps"):
            gm_day["steps"] = round(day_steps_obj["totalSteps"])

        # Resumo diário (Passos, FC Repouso e Body Battery)
        daily_sum = api_get("/usersummary-service/usersummary/daily", access_token, {"calendarDate": target_date})
        if isinstance(daily_sum, dict):
            summary_dto = daily_sum.get("userDailySummary") or daily_sum
            if summary_dto.get("totalSteps"):
                gm_day["steps"] = round(summary_dto["totalSteps"])
            if summary_dto.get("restingHeartRate"):
                gm_day["restingHr"] = round(summary_dto["restingHeartRate"])
            bb = (
                summary_dto.get("bodyBatteryHighestValue")
                or summary_dto.get("bodyBatteryMostRecentValue")
                or 0
            )
            if bb:
                gm_day["bodyBattery"] = round(bb)

        # Aplica regras de Auto-Check nos hábitos do dia
        has_workout = gm_day["sport"] != "none" or gm_day["durationMin"] > 0 or gm_day["distanceKm"] > 0
        has_sleep = gm_day["sleepHours"] >= 7.5 or gm_day["sleepScore"] >= 80
        has_steps = gm_day["steps"] >= 8000

        for h in state.get("habits", []):
            rule = h.get("autoRule", "none")
            if rule == "garmin_workout" and has_workout:
                h["days"][day_idx] = True
            elif rule == "garmin_sleep" and has_sleep:
                h["days"][day_idx] = True
            elif rule == "garmin_steps" and has_steps:
                h["days"][day_idx] = True

        print(f"   [{target_date}] Treino: {gm_day['title'] or 'Nenhum'} | Sono: {gm_day['sleepHours']}h | Passos: {gm_day['steps']}")

    # Atualiza snapshot da semana atual no histórico
    state.setdefault("history", {})[monday_key] = {
        "weekKey": monday_key,
        "archivedAt": datetime.utcnow().isoformat() + "Z",
        "habits": state.get("habits", []),
        "garminWeek": state["garminWeek"]
    }
    state["updatedAt"] = datetime.utcnow().isoformat() + "Z"

    with open(MEMORIA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    print("4. memoria.json atualizado com sucesso!")


if __name__ == "__main__":
    main()
