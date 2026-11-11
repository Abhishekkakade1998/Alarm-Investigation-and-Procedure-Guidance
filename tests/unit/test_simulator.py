import importlib


def test_active_alarms_do_not_have_end_time():
    simulator = importlib.import_module("apps.backend.simulator")
    alarms = simulator.generate_synthetic_alarms("A001")

    active_alarms = [alarm for alarm in alarms if alarm["status"] == "active"]

    assert active_alarms
    assert all(alarm["end_time"] is None for alarm in active_alarms)


def test_repeated_alarm_template_has_consistent_recurrence_pattern():
    simulator = importlib.import_module("apps.backend.simulator")
    alarms = [
        alarm
        for alarm in simulator.SYNTHETIC_ALARMS["A003"]
        if alarm["alarm_name"] == "High Discharge Pressure"
    ]

    assert len(alarms) == 3
    assert all(alarm["recurring"] is True and alarm["count"] == 3 for alarm in alarms)
    assert [alarm["severity"] for alarm in alarms] == ["low", "medium", "high"]
    timestamps = [
        simulator._parse_datetime(alarm["start_time"])
        for alarm in alarms
    ]
    assert all(
        (timestamps[index + 1] - timestamps[index]).days == 30
        for index in range(len(timestamps) - 1)
    )


def test_alarm_api_auth_filters_pagination_and_trace_headers():
    from fastapi.testclient import TestClient

    simulator = importlib.import_module("apps.backend.simulator")
    client = TestClient(simulator.app)

    assert client.get("/assets/search", params={"query": "Boiler"}).status_code == 401

    headers = {
        "Authorization": "Bearer demo-token",
        "trace_id": "investigation-123",
        "x-client-id": "copilot",
        "x-metadata-tag": "alarm-review",
    }
    search = client.get(
        "/assets/search",
        params={"query": "Boiler", "unit": "Unit 1", "site": "EastRefinery"},
        headers=headers,
    )
    assert search.status_code == 200
    assert [asset["asset_id"] for asset in search.json()["results"]] == ["A001", "A002"]

    alarms = client.get(
        "/alarms",
        params={
            "asset_id": "A001",
            "status": "active",
            "page": 1,
            "page_size": 2,
            "sort_by": "start_time",
            "sort_order": "desc",
        },
        headers=headers,
    )
    assert alarms.status_code == 200
    assert alarms.headers["trace_id"] == "investigation-123"
    assert alarms.headers["x-client-id"] == "copilot"
    assert alarms.headers["x-metadata-tag"] == "alarm-review"
    assert alarms.json()["total"] == 5
    assert all(alarm["status"] == "active" for alarm in alarms.json()["data"])

    site_alarms = client.get(
        "/alarms",
        params={"site": "EastRefinery", "page_size": 200},
        headers=headers,
    )
    assert site_alarms.status_code == 200
    expected_asset_ids = {
        asset_id
        for asset_id, asset in simulator.ASSETS.items()
        if asset["location"] == "EastRefinery"
    }
    assert site_alarms.json()["total"] > 0
    assert {
        alarm["asset_id"] for alarm in site_alarms.json()["data"]
    } <= expected_asset_ids

    invalid_page = client.get(
        "/alarms",
        params={"asset_id": "A001", "page": 0},
        headers=headers,
    )
    assert invalid_page.status_code == 422


def test_asset_metadata_temperature_limit_matches_pump_procedure():
    from fastapi.testclient import TestClient

    simulator = importlib.import_module("apps.backend.simulator")
    client = TestClient(simulator.app)
    response = client.get(
        "/assets/A001/metadata",
        headers={"Authorization": f"Bearer {simulator.VALID_TOKEN}"},
    )

    assert response.status_code == 200
    assert response.json()["kpi_targets"]["max_temp"] == 185


def test_alarm_analytics_use_request_filters_and_calculation_jobs():
    from fastapi.testclient import TestClient

    simulator = importlib.import_module("apps.backend.simulator")
    client = TestClient(simulator.app)
    headers = {"Authorization": "Bearer demo-token", "trace_id": "analytics-123"}
    window = {
        "start_time": "2000-01-01T00:00:00Z",
        "end_time": "2100-01-01T00:00:00Z",
    }

    summary = client.post(
        "/alarms/summary",
        headers=headers,
        json={
            "asset_ids": ["A001"],
            "time_range": window,
            "severity": ["critical"],
            "group_by": ["alarm_name"],
            "kpis": ["alarm_count"],
        },
    )
    assert summary.status_code == 200
    assert summary.json()["total_alarms"] == 3
    assert summary.json()["critical_count"] == 3
    assert summary.json()["groups"]
    assert summary.json()["trace_id"] == "analytics-123"

    trends = client.post(
        "/alarms/trends",
        headers=headers,
        json={"asset_ids": ["A001"], "time_range": window, "metrics": ["alarm_count"]},
    )
    assert trends.status_code == 200
    assert sum(bucket["alarm_count"] for bucket in trends.json()["data"]) == 15

    generated = client.post(
        "/calculation-code/generate",
        headers=headers,
        json={"calculation_type": "critical_alarm_density", "filters": {"unit": "Unit 1"}},
    )
    assert generated.status_code == 200
    calculation_id = generated.json()["calculation_id"]
    result = client.post(
        "/calculation-code/execute",
        headers=headers,
        json={"calculation_id": calculation_id},
    )
    assert result.status_code == 200
    assert result.json()["calculation_type"] == "critical_alarm_density"
    assert 0 <= result.json()["result"] <= 1
    assert result.json()["trace_id"] == "analytics-123"

    unknown_calculation = client.post(
        "/calculation-code/execute",
        headers=headers,
        json={"calculation_id": "missing"},
    )
    assert unknown_calculation.status_code == 404


def test_advanced_alarm_analytics_do_not_return_fixed_demo_answers():
    from fastapi.testclient import TestClient

    simulator = importlib.import_module("apps.backend.simulator")
    client = TestClient(simulator.app)
    headers = {"Authorization": "Bearer demo-token"}
    response = client.post(
        "/alarms/correlation",
        headers=headers,
        json={
            "asset_ids": ["A001"],
            "time_range": {
                "start_time": "2000-01-01T00:00:00Z",
                "end_time": "2100-01-01T00:00:00Z",
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["asset_pairs"] == []
    assert response.json()["confidence_score"] == 0

    alarm = simulator.SYNTHETIC_ALARMS["A001"][3]
    score = client.post(
        "/alarms/priority-score",
        headers=headers,
        json={"alarm_id": alarm["alarm_id"]},
    )
    assert score.status_code == 200
    assert score.json()["alarm_id"] == alarm["alarm_id"]
    assert score.json()["score"] != 0.85
