"""Alarm Management API Simulator - Synthetic Data & Endpoints"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from enum import Enum
from fastapi import FastAPI, Header, Query, HTTPException, Depends
from pydantic import BaseModel, Field
import uuid

logger = logging.getLogger(__name__)

app = FastAPI(title="Alarm Management API Simulator", version="1.0.0")


@app.middleware("http")
async def propagate_trace_metadata(request, call_next):
    trace_id = request.headers.get("trace_id") or request.headers.get("x-trace-id")
    client_id = request.headers.get("x-client-id")
    metadata_tag = request.headers.get("x-metadata-tag")
    response = await call_next(request)
    if trace_id:
        response.headers["trace_id"] = trace_id
        response.headers["x-trace-id"] = trace_id
    if client_id:
        response.headers["x-client-id"] = client_id
    if metadata_tag:
        response.headers["x-metadata-tag"] = metadata_tag
    logger.info(
        "Alarm API request completed",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "trace_id": trace_id,
            "client_id": client_id,
        },
    )
    return response


# Auth
VALID_TOKEN = os.getenv("ALARM_API_TOKEN", "demo-token")

def verify_token(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization[7:]
    if token != VALID_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    return token

# Models
class AssetSearchResult(BaseModel):
    asset_id: str
    asset_name: str
    asset_type: str
    unit: str
    location: str
    criticality: str

class AssetMetadata(BaseModel):
    asset_id: str
    asset_name: str
    asset_type: str
    unit: str
    location: str
    criticality: str
    manufacturer: str
    model: str
    installed_date: str
    last_maintenance: str
    related_assets: List[str]
    kpi_targets: Dict[str, Any]

class Alarm(BaseModel):
    alarm_id: str
    asset_id: str
    asset_name: str
    alarm_name: str
    description: str
    severity: str
    status: str
    start_time: str
    end_time: Optional[str] = None
    ack_time: Optional[str] = None
    ack_user: Optional[str] = None
    count: int
    recurring: bool

class AlarmSummary(BaseModel):
    asset_id: str
    time_range: Dict[str, str]
    total_alarms: int
    critical_count: int
    high_count: int
    medium_count: int
    recurring_rate: float
    avg_ack_delay_minutes: float
    top_alarm_names: List[str]

class AlarmTrend(BaseModel):
    timestamp: str
    alarm_count: int
    avg_ack_delay_minutes: float

class CorrelationResult(BaseModel):
    method: str
    asset_pairs: List[Dict[str, Any]]
    root_cause_candidates: List[str]
    confidence_score: float

class PriorityScore(BaseModel):
    alarm_id: str
    score: float
    reason: str
    recommendations: List[str]

class OperatorRecommendation(BaseModel):
    alarm_id: str
    immediate_actions: List[str]
    preventive_measures: List[str]
    related_assets: List[str]
    documentation_references: List[str]

class KPIDefinition(BaseModel):
    kpi_name: str
    description: str
    unit: str
    calculation: str

# Synthetic Data Generator
ASSETS = {
    "A001": {"asset_name": "Boiler Feed Pump 101", "type": "Pump", "unit": "Unit 1", "location": "EastRefinery", "criticality": "high", "related": ["A002", "A003"]},
    "A002": {"asset_name": "Boiler Feed Pump 102", "type": "Pump", "unit": "Unit 1", "location": "EastRefinery", "criticality": "high", "related": ["A001", "A003"]},
    "A003": {"asset_name": "Main Compressor", "type": "Compressor", "unit": "Unit 2", "location": "EastRefinery", "criticality": "critical", "related": ["A001", "A002"]},
    "A004": {"asset_name": "Motor M-101", "type": "Motor", "unit": "Unit 1", "location": "EastRefinery", "criticality": "high", "related": ["A001"]},
    "A005": {"asset_name": "Heat Exchanger HX-1", "type": "HX", "unit": "Unit 3", "location": "WestRefinery", "criticality": "medium", "related": ["A003"]},
}

ALARM_TEMPLATES = [
    {"name": "High Discharge Pressure", "desc": "Discharge pressure exceeding setpoint", "type": "process"},
    {"name": "Vibration Alarm", "desc": "Excessive vibration detected", "type": "device"},
    {"name": "Temperature High", "desc": "Operating temperature above safe limit", "type": "process"},
    {"name": "Flow Rate Low", "desc": "Flow rate below minimum threshold", "type": "process"},
    {"name": "Motor Trip", "desc": "Motor protection trip activated", "type": "safety"},
]

def generate_synthetic_alarms(asset_id: str, days_back: int = 90) -> List[Dict[str, Any]]:
    alarms = []
    base_time = datetime.now(timezone.utc) - timedelta(days=days_back)
    template_counts = {
        template["name"]: sum(
            candidate["name"] == template["name"]
            for candidate in ALARM_TEMPLATES
        )
        * (15 // len(ALARM_TEMPLATES))
        for template in ALARM_TEMPLATES
    }
    for i in range(15):
        template = ALARM_TEMPLATES[i % len(ALARM_TEMPLATES)]
        start = base_time + timedelta(days=i*6)
        status = ["active", "cleared", "acknowledged"][i % 3]
        alarms.append({
            "alarm_id": f"ALM-{asset_id}-{i:03d}",
            "asset_id": asset_id,
            "asset_name": ASSETS[asset_id]["asset_name"],
            "alarm_name": template["name"],
            "alarm_type": template["type"],
            "description": template["desc"],
            "severity": ["low", "medium", "high", "critical"][i % 4],
            "status": status,
            "start_time": start.isoformat().replace("+00:00", "Z"),
            "end_time": (
                (start + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
                if status == "cleared" else None
            ),
            "ack_time": (
                (start + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
                if i % 2 == 0 else None
            ),
            "ack_user": "operator-1" if i % 2 == 0 else None,
            "count": template_counts[template["name"]],
            "recurring": template_counts[template["name"]] > 1,
        })
    return alarms

SYNTHETIC_ALARMS = {aid: generate_synthetic_alarms(aid) for aid in ASSETS.keys()}
CALCULATION_JOBS: Dict[str, Dict[str, Any]] = {}


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"Invalid ISO timestamp: {value}") from error
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _filtered_alarms(
    asset_ids: Optional[List[str]] = None,
    site: Optional[str] = None,
    unit: Optional[str] = None,
    status: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    severities: Optional[List[str]] = None,
    alarm_types: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    start = _parse_datetime(start_time)
    end = _parse_datetime(end_time)
    if start and end and start > end:
        raise HTTPException(status_code=422, detail="start_time must not be after end_time")
    if asset_ids:
        unknown = set(asset_ids) - set(ASSETS)
        if unknown:
            raise HTTPException(
                status_code=404,
                detail=f"Asset not found: {', '.join(sorted(unknown))}",
            )

    selected = []
    for asset_id, asset_alarms in SYNTHETIC_ALARMS.items():
        asset = ASSETS[asset_id]
        if asset_ids and asset_id not in asset_ids:
            continue
        if site and asset["location"].casefold() != site.casefold():
            continue
        if unit and asset["unit"].casefold() != unit.casefold():
            continue
        for alarm in asset_alarms:
            alarm_time = _parse_datetime(alarm["start_time"])
            if status and alarm["status"].casefold() != status.casefold():
                continue
            if start and alarm_time < start:
                continue
            if end and alarm_time > end:
                continue
            if severities and alarm["severity"].casefold() not in {
                item.casefold() for item in severities
            }:
                continue
            if alarm_types and alarm.get("alarm_type", "").casefold() not in {
                item.casefold() for item in alarm_types
            }:
                continue
            selected.append(alarm)
    return selected


def _time_range(request: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    period = request.get("time_range") or {}
    return (
        request.get("start_time") or period.get("start_time"),
        request.get("end_time") or period.get("end_time"),
    )


def _group_alarms(alarms: List[Dict[str, Any]], fields: List[str]) -> List[Dict[str, Any]]:
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for alarm in alarms:
        key = tuple(alarm.get(field) for field in fields)
        groups.setdefault(key, []).append(alarm)
    return [
        {
            **dict(zip(fields, key)),
            "alarm_count": len(group),
            "critical_count": sum(a["severity"] == "critical" for a in group),
        }
        for key, group in groups.items()
    ]

# Endpoints
@app.get("/health")
async def health():
    return {"status": "ok", "service": "alarm-api-simulator"}

@app.get("/assets/search")
async def search_assets(
    query: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=100),
    unit: Optional[str] = Query(None),
    site: Optional[str] = Query(None),
    token: str = Depends(verify_token)
) -> Dict[str, Any]:
    results = []
    for aid, asset in ASSETS.items():
        if (
            query.casefold() in asset["asset_name"].casefold()
            and (not unit or asset["unit"].casefold() == unit.casefold())
            and (not site or asset["location"].casefold() == site.casefold())
        ):
            results.append({
                "asset_id": aid,
                "asset_name": asset["asset_name"],
                "asset_type": asset["type"],
                "unit": asset["unit"],
                "location": asset["location"],
                "criticality": asset["criticality"],
            })
    return {"total": len(results), "results": results[:limit]}

@app.get("/assets/{asset_id}/metadata")
async def get_asset_metadata(asset_id: str, token: str = Depends(verify_token)) -> Dict[str, Any]:
    if asset_id not in ASSETS:
        raise HTTPException(status_code=404, detail="Asset not found")
    asset = ASSETS[asset_id]
    return {
        "asset_id": asset_id,
        "asset_name": asset["asset_name"],
        "asset_type": asset["type"],
        "unit": asset["unit"],
        "location": asset["location"],
        "criticality": asset["criticality"],
        "manufacturer": "ABB",
        "model": f"MODEL-{asset['type']}-2024",
        "installed_date": "2020-01-15",
        "last_maintenance": "2026-10-15",
        "related_assets": asset["related"],
        "kpi_targets": {"max_temp": 185, "min_flow": 100, "max_vibration": 5}
    }

@app.get("/alarms")
async def get_alarms(
    asset_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    sort_by: str = Query("start_time"),
    sort_order: str = Query("desc"),
    status: Optional[str] = Query(None),
    site: Optional[str] = Query(None),
    unit: Optional[str] = Query(None),
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
    token: str = Depends(verify_token)
) -> Dict[str, Any]:
    if asset_id and asset_id not in ASSETS:
        raise HTTPException(status_code=404, detail="Asset not found")
    if sort_by not in {"start_time", "severity", "status", "alarm_name", "count"}:
        raise HTTPException(status_code=422, detail="Unsupported sort_by field")
    if sort_order.casefold() not in {"asc", "desc"}:
        raise HTTPException(status_code=422, detail="sort_order must be 'asc' or 'desc'")

    alarms = _filtered_alarms(
        asset_ids=[asset_id] if asset_id else None,
        site=site,
        unit=unit,
        status=status,
        start_time=start_time,
        end_time=end_time,
    )
    alarms.sort(
        key=lambda alarm: alarm[sort_by],
        reverse=sort_order.casefold() == "desc",
    )
    total = len(alarms)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        "data": alarms[start:end]
    }

@app.get("/alarms/{alarm_id}")
async def get_alarm_by_id(alarm_id: str, token: str = Depends(verify_token)) -> Dict[str, Any]:
    for alarms in SYNTHETIC_ALARMS.values():
        for alarm in alarms:
            if alarm["alarm_id"] == alarm_id:
                return alarm
    raise HTTPException(status_code=404, detail="Alarm not found")

@app.post("/alarms/summary")
async def alarm_summary(
    request: Dict[str, Any],
    trace_id: Optional[str] = Header(None, alias="trace_id"),
    x_client_id: Optional[str] = Header(None),
    x_metadata_tag: Optional[str] = Header(None),
    token: str = Depends(verify_token)
) -> Dict[str, Any]:
    asset_ids = request.get("asset_ids", [])
    start_time, end_time = _time_range(request)
    all_alarms = _filtered_alarms(
        asset_ids=asset_ids or None,
        site=request.get("site"),
        unit=request.get("unit"),
        start_time=start_time,
        end_time=end_time,
        severities=request.get("severity"),
        alarm_types=request.get("alarm_types"),
    )
    critical = sum(1 for a in all_alarms if a["severity"] == "critical")
    high = sum(1 for a in all_alarms if a["severity"] == "high")
    recurring = sum(1 for a in all_alarms if a["recurring"])
    ack_delays = [
        (
            _parse_datetime(alarm["ack_time"]) - _parse_datetime(alarm["start_time"])
        ).total_seconds() / 60
        for alarm in all_alarms
        if alarm.get("ack_time")
    ]
    group_by = request.get("group_by", [])

    return {
        "asset_ids": asset_ids,
        "time_range": {"start_time": start_time, "end_time": end_time},
        "total_alarms": len(all_alarms),
        "critical_count": critical,
        "high_count": high,
        "medium_count": len(all_alarms) - critical - high,
        "recurring_rate": recurring / len(all_alarms) if all_alarms else 0,
        "avg_ack_delay_minutes": sum(ack_delays) / len(ack_delays) if ack_delays else 0,
        "top_alarm_names": [a["alarm_name"] for a in all_alarms[:5]],
        "groups": _group_alarms(all_alarms, group_by) if group_by else [],
        "kpis": {
            "alarm_count": len(all_alarms),
            "critical_count": critical,
            "recurring_rate": recurring / len(all_alarms) if all_alarms else 0,
            "avg_ack_delay": sum(ack_delays) / len(ack_delays) if ack_delays else 0,
        },
        "trace_id": trace_id,
        "x_client_id": x_client_id,
        "x_metadata_tag": x_metadata_tag,
    }

@app.post("/alarms/trends")
async def alarm_trends(request: Dict[str, Any], token: str = Depends(verify_token)) -> Dict[str, Any]:
    start_time, end_time = _time_range(request)
    alarms = _filtered_alarms(
        asset_ids=request.get("asset_ids") or None,
        site=request.get("site"),
        unit=request.get("unit"),
        start_time=start_time,
        end_time=end_time,
    )
    bucket = request.get("bucket", "daily")
    if bucket not in {"hourly", "daily", "weekly"}:
        raise HTTPException(status_code=422, detail="bucket must be hourly, daily, or weekly")
    metrics = request.get("metrics", ["alarm_count", "avg_ack_delay"])
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for alarm in alarms:
        timestamp = _parse_datetime(alarm["start_time"])
        if bucket == "hourly":
            timestamp = timestamp.replace(minute=0, second=0, microsecond=0)
        elif bucket == "daily":
            timestamp = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            timestamp = (timestamp - timedelta(days=timestamp.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        grouped.setdefault(timestamp.isoformat(), []).append(alarm)

    data = []
    for timestamp, group in sorted(grouped.items()):
        entry: Dict[str, Any] = {"timestamp": timestamp}
        if "alarm_count" in metrics:
            entry["alarm_count"] = len(group)
        if "avg_ack_delay" in metrics or "avg_ack_delay_minutes" in metrics:
            delays = [
                (_parse_datetime(a["ack_time"]) - _parse_datetime(a["start_time"]))
                .total_seconds() / 60
                for a in group if a.get("ack_time")
            ]
            entry["avg_ack_delay"] = sum(delays) / len(delays) if delays else 0
        data.append(entry)
    return {"bucket": bucket, "metrics": metrics, "data": data}

@app.post("/alarms/correlation")
async def alarm_correlation(
    request: Dict[str, Any],
    trace_id: Optional[str] = Header(None, alias="trace_id"),
    x_client_id: Optional[str] = Header(None),
    token: str = Depends(verify_token)
) -> Dict[str, Any]:
    asset_ids = request.get("asset_ids", [])
    if len(asset_ids) > 10:
        raise HTTPException(status_code=422, detail="A maximum of 10 assets can be correlated")
    start_time, end_time = _time_range(request)
    selected = _filtered_alarms(
        asset_ids=asset_ids or None,
        start_time=start_time,
        end_time=end_time,
    )
    lag_window = request.get("lag_window_minutes", 15)
    min_support = request.get("min_support", 1)
    if lag_window < 0 or min_support < 1:
        raise HTTPException(status_code=422, detail="Invalid correlation window or support")
    by_asset = {
        asset_id: [a for a in selected if a["asset_id"] == asset_id]
        for asset_id in asset_ids
    }
    pairs = []
    root_causes = set()
    for index, asset_a in enumerate(asset_ids):
        for asset_b in asset_ids[index + 1:]:
            matched_types = []
            for alarm_a in by_asset[asset_a]:
                for alarm_b in by_asset[asset_b]:
                    time_delta = abs(
                        (_parse_datetime(alarm_a["start_time"]) -
                         _parse_datetime(alarm_b["start_time"])).total_seconds()
                    ) / 60
                    if (
                        alarm_a["alarm_type"] == alarm_b["alarm_type"]
                        and time_delta <= lag_window
                    ):
                        matched_types.append(alarm_a["alarm_name"])
                        root_causes.add(alarm_a["alarm_name"])
            if len(matched_types) >= min_support:
                pairs.append({
                    "asset1": asset_a,
                    "asset2": asset_b,
                    "correlation": len(matched_types) / max(
                        len(by_asset[asset_a]), len(by_asset[asset_b]), 1
                    ),
                    "support": len(matched_types),
                    "shared_alarm_types": sorted(set(matched_types)),
                })
    return {
        "method": request.get("correlation_method", "cooccurrence"),
        "asset_pairs": pairs,
        "root_cause_candidates": sorted(root_causes),
        "confidence_score": max(
            (pair["correlation"] for pair in pairs), default=0.0
        ),
        "trace_id": trace_id,
        "x_client_id": x_client_id,
    }

@app.post("/alarms/flood-analysis")
async def flood_analysis(request: Dict[str, Any], token: str = Depends(verify_token)) -> Dict[str, Any]:
    start_time, end_time = _time_range(request)
    alarms = _filtered_alarms(
        site=request.get("site"),
        unit=request.get("unit"),
        start_time=start_time,
        end_time=end_time,
    )
    window_minutes = request.get("rolling_window_minutes", 10)
    threshold = request.get("threshold_count", 10)
    if window_minutes < 1 or threshold < 1:
        raise HTTPException(status_code=422, detail="Window and threshold must be positive")
    timestamps = sorted(_parse_datetime(a["start_time"]) for a in alarms)
    max_count = 0
    left = 0
    for right, timestamp in enumerate(timestamps):
        while (timestamp - timestamps[left]).total_seconds() > window_minutes * 60:
            left += 1
        max_count = max(max_count, right - left + 1)
    detected = max_count >= threshold
    return {
        "unit": request.get("unit"),
        "flood_detected": detected,
        "alarm_count_in_window": max_count,
        "threshold": threshold,
        "severity": "high" if detected else "normal",
        "recommendation": (
            "Investigate alarm flood according to site procedure"
            if detected else "No alarm flood detected in the requested window"
        ),
    }

@app.post("/alarms/rationalization-candidates")
async def rationalization_candidates(request: Dict[str, Any], token: str = Depends(verify_token)) -> Dict[str, Any]:
    start_time, end_time = _time_range(request)
    alarms = _filtered_alarms(
        asset_ids=request.get("asset_ids") or None,
        site=request.get("site"),
        unit=request.get("unit"),
        start_time=start_time,
        end_time=end_time,
    )
    recurrence_threshold = request.get("recurrence_threshold", 5)
    stale_threshold = request.get("stale_minutes_threshold", 180)
    candidates = [
        {
            "alarm_id": alarm["alarm_id"],
            "alarm_name": alarm["alarm_name"],
            "asset_id": alarm["asset_id"],
            "recurrence_count": alarm["count"],
            "status": alarm["status"],
        }
        for alarm in alarms
        if alarm["recurring"] and alarm["count"] >= recurrence_threshold
    ]
    return {
        "recurrence_threshold": recurrence_threshold,
        "stale_minutes_threshold": stale_threshold,
        "candidates": candidates,
    }

@app.post("/alarms/priority-score")
async def priority_score(request: Dict[str, Any], token: str = Depends(verify_token)) -> Dict[str, Any]:
    alarm = next(
        (
            alarm for alarms in SYNTHETIC_ALARMS.values()
            for alarm in alarms if alarm["alarm_id"] == request.get("alarm_id")
        ),
        None,
    )
    if alarm is None:
        raise HTTPException(status_code=404, detail="Alarm not found")
    severity_score = {"low": 0.2, "medium": 0.4, "high": 0.7, "critical": 0.9}
    score = min(
        1.0,
        severity_score[alarm["severity"]]
        + (0.05 if alarm["recurring"] else 0)
        + min(alarm["count"] * 0.01, 0.05),
    )
    return {
        "alarm_id": alarm["alarm_id"],
        "score": score,
        "reason": (
            f"{alarm['severity']} severity"
            + (", recurring" if alarm["recurring"] else "")
            + f", count {alarm['count']}"
        ),
        "recommendations": [
            "Verify the alarm against current process values and approved limits",
            "Follow the approved site response procedure",
        ],
    }

@app.post("/recommendations/operator-actions")
async def operator_recommendations(
    request: Dict[str, Any],
    trace_id: Optional[str] = Header(None, alias="trace_id"),
    x_client_id: Optional[str] = Header(None),
    token: str = Depends(verify_token)
) -> Dict[str, Any]:
    alarm_id = request.get("alarm_id")
    alarm = next(
        (
            alarm for alarms in SYNTHETIC_ALARMS.values()
            for alarm in alarms if alarm["alarm_id"] == alarm_id
        ),
        None,
    )
    if alarm is None:
        raise HTTPException(status_code=404, detail="Alarm not found")
    asset = ASSETS[alarm["asset_id"]]
    return {
        "alarm_id": alarm_id,
        "immediate_actions": [
            f"Verify the {alarm['alarm_name']} indication against current instrument readings",
            "Follow the approved site procedure and operating limits before taking action",
        ],
        "preventive_measures": ["Schedule preventive maintenance", "Review operating procedures"],
        "related_assets": asset["related"] if request.get("include_related", True) else [],
        "documentation_references": ["SOP-101", "Maintenance-Manual-Ch3"],
        "trace_id": trace_id,
        "x_client_id": x_client_id,
    }

@app.post("/calculation-code/generate")
async def generate_calculation_code(request: Dict[str, Any], token: str = Depends(verify_token)) -> Dict[str, Any]:
    supported_types = {
        "alarm_flood_index",
        "critical_alarm_density",
        "operator_response_efficiency",
        "nuisance_alarm_score",
    }
    calculation_type = request.get("calculation_type")
    if calculation_type not in supported_types:
        raise HTTPException(status_code=422, detail="Unsupported calculation_type")
    calc_id = str(uuid.uuid4())[:8]
    CALCULATION_JOBS[calc_id] = {
        "calculation_type": calculation_type,
        "filters": request.get("filters", {}),
    }
    return {
        "calculation_id": calc_id,
        "calculation_type": calculation_type,
        "code": f"server-side calculation: {calculation_type}",
        "status": "ready"
    }

@app.post("/calculation-code/execute")
async def execute_calculation_code(
    request: Dict[str, Any],
    trace_id: Optional[str] = Header(None, alias="trace_id"),
    x_client_id: Optional[str] = Header(None),
    token: str = Depends(verify_token)
) -> Dict[str, Any]:
    calculation_id = request.get("calculation_id")
    job = CALCULATION_JOBS.get(calculation_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Calculation not found or expired")
    filters = {**job["filters"], **request.get("filters", {})}
    start_time, end_time = _time_range(filters)
    alarms = _filtered_alarms(
        site=filters.get("site"),
        unit=filters.get("unit"),
        start_time=start_time,
        end_time=end_time,
    )
    count = len(alarms)
    if job["calculation_type"] == "alarm_flood_index":
        result = count / max(len({alarm["asset_id"] for alarm in alarms}), 1)
        unit_name = "alarms_per_asset"
    elif job["calculation_type"] == "critical_alarm_density":
        result = sum(alarm["severity"] == "critical" for alarm in alarms) / max(count, 1)
        unit_name = "ratio"
    elif job["calculation_type"] == "operator_response_efficiency":
        delays = [
            (_parse_datetime(a["ack_time"]) - _parse_datetime(a["start_time"]))
            .total_seconds() / 60
            for a in alarms if a.get("ack_time")
        ]
        result = 1 / (1 + (sum(delays) / len(delays) if delays else 0))
        unit_name = "ratio"
    else:
        result = sum(alarm["recurring"] for alarm in alarms) / max(count, 1)
        unit_name = "ratio"
    return {
        "calculation_id": calculation_id,
        "calculation_type": job["calculation_type"],
        "result": result,
        "unit": unit_name,
        "trace_id": trace_id,
        "x_client_id": x_client_id,
    }

@app.get("/analytics/kpi-definitions")
async def kpi_definitions(token: str = Depends(verify_token)) -> Dict[str, Any]:
    return {
        "kpis": [
            {"kpi_name": "alarm_count", "description": "Total alarm count", "unit": "count"},
            {"kpi_name": "recurring_rate", "description": "Recurring alarm percentage", "unit": "%"},
            {"kpi_name": "avg_ack_delay", "description": "Average acknowledgement delay", "unit": "minutes"},
        ]
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
