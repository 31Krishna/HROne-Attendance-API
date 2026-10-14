"""

Employee Attendance & Analytics API - STARTER



Run:  uvicorn app.main:app --port 8000

Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)



This file was written quickly by a colleague who has left the company. The happy path

works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,

openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.

"""

import os

from datetime import date, datetime, time, timedelta, timezone

from typing import Literal,Optional

from bson import json_util



from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Query, Response

from pydantic import BaseModel

from pymongo import MongoClient

from pymongo.errors import DuplicateKeyError

from datetime import date

from pymongo.errors import DuplicateKeyError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from decimal import Decimal, ROUND_HALF_UP





MIN_EPOCH_MS = 100_000_000_000

MAX_EPOCH_MS = 4_102_444_800_000



load_dotenv()



client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))

db = client[os.getenv("MONGO_DB", "attendance_db")]



db.employees.create_index(

    [("emp_code", 1)],

    unique=True,

    name="uq_employees_emp_code",

)



db.employees.create_index(

    [("department", 1), ("joined_on", 1)],

    name="idx_employees_department_joined",

)



db.attendance_logs.create_index(

    [("emp_code", 1), ("date", 1)],

    unique=True,

    name="uq_attendance_emp_date",

)



db.attendance_logs.create_index(

    [("date", -1), ("emp_code", 1)],

    name="idx_attendance_date_emp",

)



db.attendance_logs.create_index(

    [("date", 1), ("late_minutes", 1), ("emp_code", 1)],

    name="idx_attendance_date_late_emp",

)





UTC = timezone.utc

IST = timezone(timedelta(hours=5, minutes=30))



app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")





# --------------------------------------------------------------------------- #

# Helpers

# --------------------------------------------------------------------------- #

def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:

    h, m = map(int, shift_start.split(":"))

    start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)

    minutes = int((punch_in - start).total_seconds() / 60)

    return minutes if minutes > 10 else 0





def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:

    return round((punch_out - punch_in).total_seconds() / 3600, 2)





def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:

    h, m = map(int, shift_end.split(":"))

    end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)

    return max(0, int((punch_out - end).total_seconds() / 60))





def _shift_datetimes(attendance_day: str, employee: dict):

    day = date.fromisoformat(attendance_day)



    sh, sm = map(int, employee["shift_start"].split(":"))

    eh, em = map(int, employee["shift_end"].split(":"))



    start_local = datetime.combine(day, time(sh, sm), IST)



    end_day = (

        day + timedelta(days=1)

        if (eh, em) <= (sh, sm)

        else day

    )



    end_local = datetime.combine(end_day, time(eh, em), IST)



    start_utc = start_local.astimezone(UTC).replace(tzinfo=None)

    end_utc = end_local.astimezone(UTC).replace(tzinfo=None)



    return start_utc, end_utc





def _late_minutes(

    punch_in: datetime | None,

    attendance_day: str,

    employee: dict,

) -> int:

    if punch_in is None:

        return 0



    start_utc, _ = _shift_datetimes(attendance_day, employee)

    seconds = (punch_in - start_utc).total_seconds()



    return int(seconds // 60) if seconds > 600 else 0





def _recompute(record: dict, employee: dict) -> None:

    status = record.get("status")

    punch_in = record.get("punch_in")

    punch_out = record.get("punch_out")



    # ABSENT and LEAVE records cannot contain punch times.

    if status not in {"PRESENT", "WFH", "ON_DUTY"}:

        record.update({

            "punch_in": None,

            "punch_out": None,

            "work_hours": None,

            "late_minutes": 0,

            "overtime_minutes": 0,

            "half_day": False,

        })

        return



    record["late_minutes"] = _late_minutes(

        punch_in,

        record["date"],

        employee,

    )



    if punch_in is not None and punch_out is not None:

        hours = round(

            (punch_out - punch_in).total_seconds() / 3600,

            2,

        )



        record["work_hours"] = hours

        record["overtime_minutes"] = _overtime_minutes(

            punch_out,

            record["date"],

            employee,

        )

        record["half_day"] = hours < 4.5

    else:

        record["work_hours"] = None

        record["overtime_minutes"] = 0

        record["half_day"] = False





def _overtime_minutes(

    punch_out: datetime | None,

    attendance_day: str,

    employee: dict,

) -> int:

    if punch_out is None:

        return 0



    _, shift_end_utc = _shift_datetimes(attendance_day, employee)

    seconds = (punch_out - shift_end_utc).total_seconds()



    minutes = int(seconds // 60)

    return minutes if minutes >= 30 else 0





def _epoch_to_dt(value: int) -> datetime:

    if type(value) is not int:

        raise HTTPException(

            status_code=422,

            detail="Timestamp must be integer epoch milliseconds",

        )



    try:

        return datetime.fromtimestamp(

            value / 1000,

            tz=timezone.utc,

        ).replace(tzinfo=None, microsecond=0)

    except (OverflowError, OSError, ValueError):

        raise HTTPException(

            status_code=422,

            detail="Invalid timestamp",

        )

def _attendance_date(punch_in: datetime, employee: dict) -> str:

    """Return the attendance date in IST, accounting for overnight shifts."""

    ist_punch = punch_in.replace(tzinfo=timezone.utc).astimezone(IST)



    shift_start = datetime.strptime(

        employee["shift_start"], "%H:%M"

    ).time()



    shift_end = datetime.strptime(

        employee["shift_end"], "%H:%M"

    ).time()



    attendance_day = ist_punch.date()



    # Early-morning punches for overnight shifts belong to the previous day.

    if shift_end <= shift_start and ist_punch.time() < shift_end:

        attendance_day -= timedelta(days=1)



    return attendance_day.isoformat()





# --------------------------------------------------------------------------- #

# Models

# --------------------------------------------------------------------------- #

class EmployeeIn(BaseModel):

    emp_code: str

    name: str

    email: str

    department: str

    shift_start: str = "09:30"

    shift_end: str = "18:30"

    joined_on: str





class PunchInIn(BaseModel):

    model_config = ConfigDict(extra="ignore", strict=True)



    emp_code: str = Field(min_length=1)

    punched_at: int | None = Field(default=None, ge=100000000000)

    status: str = "PRESENT"



    @field_validator("status")

    @classmethod

    def validate_status(cls, value):

        if value not in {"PRESENT", "WFH", "ON_DUTY"}:

            raise ValueError("Invalid punch-in status")

        return value



class PunchOutIn(BaseModel):

    emp_code: str

    punched_at: Optional[int] = None





class RegularizeIn(BaseModel):

    model_config = ConfigDict(extra="ignore", strict=True)



    status: Literal[

        "PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"

    ] | None = None



    punch_in: int | None = Field(

        default=None,

        ge=MIN_EPOCH_MS,

        le=MAX_EPOCH_MS,

    )



    punch_out: int | None = Field(

        default=None,

        ge=MIN_EPOCH_MS,

        le=MAX_EPOCH_MS,

    )



    reason: str = Field(min_length=5, max_length=200)

    regularized_by: str = Field(min_length=1, max_length=50)







# --------------------------------------------------------------------------- #

# Endpoints provided

# --------------------------------------------------------------------------- #



def serialize_attendance(doc: dict) -> dict:

    result = {

        key: value

        for key, value in doc.items()

        if key != "_id"

    }



    return result





@app.get("/health")

def health():

    try:

        client.admin.command("ping")

        return {"status": "ok"}

    except Exception:

        raise HTTPException(

            status_code=503,

            detail="Database unavailable"

        )



@app.post("/employees", status_code=201)

def create_employee(body: EmployeeIn):

    doc = body.model_dump()

    doc["created_at"] = datetime.now(timezone.utc)



    try:

        db.employees.insert_one(doc)

    except DuplicateKeyError:

        raise HTTPException(

            status_code=409,

            detail="emp_code already exists"

        )



    doc.pop("_id", None)

    return doc







@app.get("/employees")

def list_employees(

    department: Optional[str] = None,

    page: int = 1,

    page_size: int = 20

):

    if page < 1:

        raise HTTPException(422, "page must be at least 1")



    if page_size < 1 or page_size > 100:

        raise HTTPException(

            422,

            "page_size must be between 1 and 100"

        )



    q = {}

    if department:

        q["department"] = department



    skip = (page - 1) * page_size

    total = db.employees.count_documents(q)



    items = list(

        db.employees.find(q, {"_id": 0})

        .sort("emp_code", 1)

        .skip(skip)

        .limit(page_size)

    )



    return {

        "items": items,

        "total": total,

        "page": page,

        "page_size": page_size

    }









@app.post("/attendance/punch-in", status_code=201)

def punch_in(body: PunchInIn):

    # 1. Find the employee

    emp = db.employees.find_one({"emp_code": body.emp_code})



    if emp is None:

        raise HTTPException(

            status_code=404,

            detail="Employee not found",

        )



    # 2. Convert the supplied epoch milliseconds to UTC,

    # or use the current UTC time.

    if body.punched_at is not None:

        ts = datetime.fromtimestamp(

            body.punched_at / 1000,

            tz=timezone.utc,

        )

    else:

        ts = datetime.now(timezone.utc)



    # 3. Truncate to whole seconds and store as naive UTC,

    # consistent with the existing database helpers.

    ts = ts.replace(microsecond=0, tzinfo=None)



    # 4. Calculate the attendance date in IST.

    ist_ts = ts.replace(tzinfo=timezone.utc).astimezone(IST)



    shift_start = datetime.strptime(

        emp["shift_start"], "%H:%M"

    ).time()

    shift_end = datetime.strptime(

        emp["shift_end"], "%H:%M"

    ).time()



    attendance_date = ist_ts.date()



    # For overnight shifts, early-morning punches belong

    # to the previous attendance date.

    if shift_end <= shift_start and ist_ts.time() < shift_end:

        attendance_date -= timedelta(days=1)



    attendance_day = attendance_date.isoformat()



    # 5. Create the attendance record.

    doc = {

        "emp_code": body.emp_code,

        "date": attendance_day,

        "status": body.status,

        "punch_in": ts,

        "punch_out": None,

        "work_hours": None,

        "late_minutes": _late_minutes(

            ts,

            attendance_day,

            emp,

        ),

        "overtime_minutes": 0,

        "half_day": False,

        "history": [],

    }



    # 6. Save the record; the unique index prevents duplicates.

    try:

        db.attendance_logs.insert_one(doc)

    except DuplicateKeyError:

        raise HTTPException(

            status_code=409,

            detail="Already punched in for this date",

        )



    # 7. Return the serialized attendance record.

    return serialize_attendance(doc)



@app.post("/attendance/punch-out")

def punch_out(body: PunchOutIn):

    # 1. Verify that the employee exists.

    emp = db.employees.find_one({"emp_code": body.emp_code})



    if emp is None:

        raise HTTPException(

            status_code=404,

            detail="Employee not found",

        )



    # 2. Convert the supplied epoch milliseconds to naive UTC.

    if body.punched_at is not None:

        ts = datetime.fromtimestamp(

            body.punched_at / 1000,

            tz=timezone.utc,

        )

    else:

        ts = datetime.now(timezone.utc)



    ts = ts.replace(microsecond=0, tzinfo=None)



    # 3. Determine the attendance date in Indian Standard Time.

    ist_ts = ts.replace(tzinfo=timezone.utc).astimezone(IST)



    shift_start = datetime.strptime(

        emp["shift_start"], "%H:%M"

    ).time()



    shift_end = datetime.strptime(

        emp["shift_end"], "%H:%M"

    ).time()



    attendance_date = ist_ts.date()



    # For overnight shifts, early-morning punches belong

    # to the previous attendance date.

    if shift_end <= shift_start and ist_ts.time() < shift_end:

        attendance_date -= timedelta(days=1)



    attendance_day = attendance_date.isoformat()



    # 4. Find the corresponding punch-in record.

    record = db.attendance_logs.find_one({

        "emp_code": body.emp_code,

        "date": attendance_day,

    })



    if record is None:

        raise HTTPException(

            status_code=404,

            detail="Punch-in record not found for this date",

        )



    if record.get("punch_out") is not None:

        raise HTTPException(

            status_code=409,

            detail="Already punched out",

        )



    punch_in_ts = record.get("punch_in")



    if punch_in_ts is None:

        raise HTTPException(

            status_code=409,

            detail="Punch-in timestamp is missing",

        )



    # 5. Reject a punch-out earlier than punch-in.

    if ts < punch_in_ts:

        raise HTTPException(

            status_code=422,

            detail="Punch-out cannot be earlier than punch-in",

        )



    # 6. Calculate work hours and overtime.

    work_hours = compute_work_hours(punch_in_ts, ts)



    _, shift_end_utc = _shift_datetimes(attendance_day, emp)



    overtime_minutes = max(

        0,

        int((ts - shift_end_utc).total_seconds() // 60),

    )



    # 7. Update atomically to prevent duplicate punch-outs.



    result = db.attendance_logs.update_one(

        {

            "_id": record["_id"],

            "punch_out": None,

        },

        {

            "$set": {

                "punch_out": ts,

                "work_hours": work_hours,

                "overtime_minutes": overtime_minutes,

            }

        },

    )



    if result.modified_count != 1:

        raise HTTPException(

            status_code=409,

            detail="Attendance record was already updated",

        )



    updated = db.attendance_logs.find_one(

        {"_id": record["_id"]}

    )



    return serialize_attendance(updated)







@app.get("/attendance")

def list_attendance(

    emp_code: Optional[str] = None,

    date_from: Optional[str] = None,

    date_to: Optional[str] = None,

    status: Optional[str] = None,

    page: int = 1,

    page_size: int = 20,

):

    if page < 1:

        raise HTTPException(422, "page must be at least 1")



    if page_size < 1 or page_size > 100:

        raise HTTPException(

            422, "page_size must be between 1 and 100"

        )



    if date_from and date_to and date_from > date_to:

        raise HTTPException(

            422, "date_from must not be after date_to"

        )



    q = {}



    if emp_code:

        q["emp_code"] = emp_code



    if date_from or date_to:

        q["date"] = {}

        if date_from:

            q["date"]["$gte"] = date_from

        if date_to:

            q["date"]["$lte"] = date_to



    if status:

        q["status"] = status



    total = db.attendance_logs.count_documents(q)

    skip = (page - 1) * page_size



    docs = list(

        db.attendance_logs.find(q, {"_id": 0})

        .sort([("date", -1), ("emp_code", 1)])

        .skip(skip)

        .limit(page_size)

    )



    return {

        "items": docs,

        "total": total,

        "page": page,

        "page_size": page_size,

    }







@app.patch("/attendance/{emp_code}/{date}")

def regularize_attendance(

    emp_code: str,

    date: str,

    body: RegularizeIn,

):

    # 1. Validate the attendance date.

    try:

        parsed_date = datetime.strptime(date, "%Y-%m-%d").date()

        if parsed_date.isoformat() != date:

            raise ValueError

    except ValueError:

        raise HTTPException(

            status_code=422,

            detail="date must be in YYYY-MM-DD format",

        )



    # 2. Validate the required audit information.

    requested = body.model_dump(exclude_unset=True)



    reason = requested.pop("reason", None)

    regularized_by = requested.pop("regularized_by", None)



    if not isinstance(reason, str) or not reason.strip():

        raise HTTPException(

            status_code=422,

            detail="reason is required",

        )



    if not isinstance(regularized_by, str) or not regularized_by.strip():

        raise HTTPException(

            status_code=422,

            detail="regularized_by is required",

        )



    reason = reason.strip()

    regularized_by = regularized_by.strip()



    allowed_fields = {"status", "punch_in", "punch_out"}

    if not requested or not set(requested).issubset(allowed_fields):

        raise HTTPException(

            status_code=422,

            detail="Provide at least one valid attendance field to update",

        )



    # 3. Verify employee and attendance record.

    emp = db.employees.find_one({"emp_code": emp_code})



    if emp is None:

        raise HTTPException(

            status_code=404,

            detail="Employee not found",

        )



    old = db.attendance_logs.find_one({

        "emp_code": emp_code,

        "date": date,

    })



    if old is None:

        raise HTTPException(

            status_code=404,

            detail="Attendance record not found",

        )



    # 4. Prepare the proposed attendance record.

    new = dict(old)



    for field in ("status", "punch_in", "punch_out"):

        if field not in requested:

            continue



        value = requested[field]



        if field in ("punch_in", "punch_out"):

            value = _epoch_to_dt(value) if value is not None else None



        new[field] = value



    status = new.get("status")

    punch_in = new.get("punch_in")

    punch_out = new.get("punch_out")



    valid_statuses = {"PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"}



    if status not in valid_statuses:

        raise HTTPException(

            status_code=422,

            detail="Invalid attendance status",

        )



    # 5. Validate status and timestamps.

    if status in {"ABSENT", "LEAVE"}:

        if punch_in is not None or punch_out is not None:

            # Allow a status-only correction to clear old punch times,

            # but reject explicitly supplied non-null punch times.

            if (

                ("punch_in" in requested and requested["punch_in"] is not None)

                or (

                    "punch_out" in requested

                    and requested["punch_out"] is not None

                )

            ):

                raise HTTPException(

                    status_code=422,

                    detail="ABSENT/LEAVE cannot have punch times",

                )



        new["punch_in"] = None

        new["punch_out"] = None



    else:

        if punch_in is None:

            raise HTTPException(

                status_code=422,

                detail="Presence status requires punch_in",

            )



        if _attendance_date(punch_in, emp) != date:

            raise HTTPException(

                status_code=422,

                detail="punch_in must belong to the attendance date",

            )



        if punch_out is not None:

            if punch_out <= punch_in:

                raise HTTPException(

                    status_code=422,

                    detail="punch_out must be after punch_in",

                )



            if punch_out - punch_in > timedelta(hours=24):

                raise HTTPException(

                    status_code=422,

                    detail="Punch duration cannot exceed 24 hours",

                )



    # 6. Recalculate derived fields.

    _recompute(new, emp)



    tracked_fields = (

        "status",

        "punch_in",

        "punch_out",

        "work_hours",

        "late_minutes",

        "overtime_minutes",

        "half_day",

    )



    changes = {}



    for field in tracked_fields:

        before = old.get(field)

        after = new.get(field)



        if before != after:

            changes[field] = {

                "from": before,

                "to": after,

            }



    if not changes:

        raise HTTPException(

            status_code=422,

            detail="The request does not change the attendance record",

        )



    # 7. Create the audit-history entry.

    history_entry = {

        "at": datetime.now(timezone.utc).replace(

            tzinfo=None,

            microsecond=0,

        ),

        "regularized_by": regularized_by,

        "reason": reason,

        "changes": changes,

    }



    # 8. Update atomically using the original field values.

    # This prevents overwriting a concurrent attendance edit.

    update_filter = {"_id": old["_id"]}



    for field in tracked_fields:

        if field in old:

            update_filter[field] = old[field]

        else:

            update_filter[field] = {"$exists": False}



    if "history" in old:

        update_filter["history"] = old["history"]

    else:

        update_filter["history"] = {"$exists": False}



    result = db.attendance_logs.update_one(

        update_filter,

        {

            "$set": {

                field: new.get(field)

                for field in tracked_fields

            },

            "$push": {

                "history": history_entry,

            },

        },

    )



    if result.modified_count != 1:

        raise HTTPException(

            status_code=409,

            detail="Attendance record changed concurrently; retry the request",

        )



    # 9. Return the updated record.

    updated = db.attendance_logs.find_one({"_id": old["_id"]})



    if updated is None:

        raise HTTPException(

            status_code=500,

            detail="Attendance was updated but could not be retrieved",

        )



    return serialize_attendance(updated)





@app.get("/analytics/employees/{emp_code}/monthly")

def employee_monthly(

    emp_code: str,

    month: str = Query(

        ...,

        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",

    ),

):

    # 1. Validate the month.

    try:

        month_start = datetime.strptime(month, "%Y-%m").date()

        if month_start.strftime("%Y-%m") != month:

            raise ValueError

    except ValueError:

        raise HTTPException(

            status_code=422,

            detail="month must be in YYYY-MM format",

        )



    # 2. Find the employee.

    employee = db.employees.find_one(

        {"emp_code": emp_code},

        {"_id": 0, "emp_code": 1, "joined_on": 1},

    )



    if employee is None:

        raise HTTPException(

            status_code=404,

            detail="Employee not found",

        )



    # 3. Calculate the month's date boundaries.

    next_month = (

        date(month_start.year + 1, 1, 1)

        if month_start.month == 12

        else date(month_start.year, month_start.month + 1, 1)

    )



    month_end = next_month - timedelta(days=1)

    joined_on = date.fromisoformat(employee["joined_on"])



    # Working days begin on the later of the month start

    # and the employee's joining date.

    effective_start = max(month_start, joined_on)



    working_days = 0

    current_day = effective_start



    while current_day <= month_end:

        if current_day.weekday() < 5:

            working_days += 1

        current_day += timedelta(days=1)



    # 4. Aggregate the employee's attendance records in MongoDB.

    first_date = max(month_start, joined_on).isoformat()

    last_date = month_end.isoformat()



    pipeline = [

        {

            "$match": {

                "emp_code": emp_code,

                "date": {

                    "$gte": first_date,

                    "$lte": last_date,

                },

            }

        },

        {

            "$set": {

                "_weekday": {

                    "$dayOfWeek": {

                        "$dateFromString": {

                            "dateString": "$date"

                        }

                    }

                },

                "_present_days": {

                    "$cond": [

                        {

                            "$in": [

                                "$status",

                                ["PRESENT", "WFH", "ON_DUTY"],

                            ]

                        },

                        {

                            "$cond": [

                                {"$eq": ["$half_day", True]},

                                0.5,

                                1,

                            ]

                        },

                        0,

                    ]

                },

            }

        },

        {

            "$group": {

                "_id": None,

                "present_days": {

                    "$sum": {

                        "$cond": [

                            {"$in": ["$_weekday", [2, 3, 4, 5, 6]]},

                            "$_present_days",

                            0,

                        ]

                    }

                },

                "leave_days": {

                    "$sum": {

                        "$cond": [

                            {"$eq": ["$status", "LEAVE"]},

                            1,

                            0,

                        ]

                    }

                },

                "late_count": {

                    "$sum": {

                        "$cond": [

                            {"$gt": ["$late_minutes", 0]},

                            1,

                            0,

                        ]

                    }

                },

                "total_late_minutes": {

                    "$sum": {"$ifNull": ["$late_minutes", 0]}

                },

                "total_overtime_minutes": {

                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}

                },

            }

        },

    ]



    result = list(db.attendance_logs.aggregate(pipeline))

    totals = result[0] if result else {}



    present_days = float(totals.get("present_days", 0))

    leave_days = int(totals.get("leave_days", 0))

    late_count = int(totals.get("late_count", 0))

    total_late_minutes = int(totals.get("total_late_minutes", 0))

    total_overtime_minutes = int(

        totals.get("total_overtime_minutes", 0)

    )



    # 5. Calculate attendance percentage using half-up rounding.

    attendance_pct = None



    if working_days > 0:

        percentage = Decimal(str(present_days)) * Decimal("100")

        percentage /= Decimal(working_days)



        attendance_pct = float(

            percentage.quantize(

                Decimal("0.0001"),

                rounding=ROUND_HALF_UP,

            )

        )



    # 6. Return the monthly summary.

    return {

        "emp_code": emp_code,

        "month": month,

        "working_days": working_days,

        "present_days": present_days,

        "leave_days": leave_days,

        "late_count": late_count,

        "total_late_minutes": total_late_minutes,

        "total_overtime_minutes": total_overtime_minutes,

        "attendance_pct": attendance_pct,

    }





@app.get("/analytics/departments/summary")

def department_summary(

    month: str = Query(

        ...,

        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",

    ),

    department: Optional[str] = None,

):

    # Validate the month.

    try:

        month_start = datetime.strptime(month, "%Y-%m").date()

        if month_start.strftime("%Y-%m") != month:

            raise ValueError

    except ValueError:

        raise HTTPException(

            status_code=422,

            detail="month must be in YYYY-MM format",

        )



    # Calculate month boundaries.

    next_month = (

        date(month_start.year + 1, 1, 1)

        if month_start.month == 12

        else date(month_start.year, month_start.month + 1, 1)

    )

    month_end = next_month - timedelta(days=1)

    first_date = month_start.isoformat()

    last_date = month_end.isoformat()



    # Include employees who joined on or before month-end.

    employee_filter = {"joined_on": {"$lte": last_date}}

    if department is not None:

        employee_filter["department"] = department



    employees = list(

        db.employees.find(

            employee_filter,

            {"_id": 0, "emp_code": 1, "department": 1, "joined_on": 1},

        )

    )



    # Group eligible employees by department.

    departments = {}

    for employee in employees:

        departments.setdefault(employee["department"], []).append(employee)



    items = []



    for department_name, department_employees in sorted(departments.items()):

        employee_codes = [e["emp_code"] for e in department_employees]

        joining_dates = {

            e["emp_code"]: e["joined_on"]

            for e in department_employees

        }



        # Fetch attendance records for these employees in the month.

        logs = list(

            db.attendance_logs.find(

                {

                    "emp_code": {"$in": employee_codes},

                    "date": {"$gte": first_date, "$lte": last_date},

                },

                {

                    "_id": 0,

                    "emp_code": 1,

                    "date": 1,

                    "status": 1,

                    "half_day": 1,

                    "work_hours": 1,

                    "late_minutes": 1,

                    "overtime_minutes": 1,

                },

            )

        )



        # Ignore any attendance record before an employee joined.

        logs = [

            log for log in logs

            if log["date"] >= joining_dates[log["emp_code"]]

        ]



        present_days = 0.0

        work_hour_values = []

        late_count = 0

        total_late_minutes = 0

        leave_count = 0

        on_duty_count = 0



        for log in logs:

            status = log.get("status")



            # Present days count only Monday-Friday.

            weekday = date.fromisoformat(log["date"]).weekday()

            if weekday < 5 and status in ("PRESENT", "WFH", "ON_DUTY"):

                present_days += 0.5 if log.get("half_day", False) else 1.0



            # Average work hours over qualifying attendance records.

            hours = log.get("work_hours")

            if status in ("PRESENT", "WFH", "ON_DUTY") and hours is not None:

                work_hour_values.append(float(hours))



            late_minutes = log.get("late_minutes") or 0

            if late_minutes > 0:

                late_count += 1

                total_late_minutes += late_minutes



            if status == "LEAVE":

                leave_count += 1



            if status == "ON_DUTY":

                on_duty_count += 1



        # Round the average using half-up rounding.

        avg_work_hours = None

        if work_hour_values:

            average = sum(

                Decimal(str(value)) for value in work_hour_values

            ) / Decimal(len(work_hour_values))



            avg_work_hours = float(

                average.quantize(

                    Decimal("0.0001"),

                    rounding=ROUND_HALF_UP,

                )

            )



        items.append({

            "department": department_name,

            "headcount": len(department_employees),

            "present_days": present_days,

            "avg_work_hours": avg_work_hours,

            "late_count": late_count,

            "total_late_minutes": total_late_minutes,

            "leave_count": leave_count,

            "on_duty_count": on_duty_count,

        })



    return {"month": month, "items": items}





@app.get("/analytics/leaderboard/late")

def late_leaderboard(

    month: str = Query(

        ...,

        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",

    ),

    limit: int = Query(10, ge=1, le=50),

    department: Optional[str] = None,

):

    # Validate the month and calculate its date range.

    try:

        month_start = datetime.strptime(month, "%Y-%m").date()

        if month_start.strftime("%Y-%m") != month:

            raise ValueError

    except ValueError:

        raise HTTPException(

            status_code=422,

            detail="month must be in YYYY-MM format",

        )



    next_month = (

        date(month_start.year + 1, 1, 1)

        if month_start.month == 12

        else date(month_start.year, month_start.month + 1, 1)

    )



    first_date = month_start.isoformat()

    last_date = (next_month - timedelta(days=1)).isoformat()



    # Aggregate lateness for each employee.

    pipeline = [

        {

            "$match": {

                "date": {

                    "$gte": first_date,

                    "$lte": last_date,

                },

                "late_minutes": {"$gt": 0},

            }

        },

        {

            "$group": {

                "_id": "$emp_code",

                "total_late_minutes": {"$sum": "$late_minutes"},

                "late_count": {"$sum": 1},

            }

        },

        {

            "$match": {

                "total_late_minutes": {"$gt": 0},

            }

        },

        {

            "$lookup": {

                "from": "employees",

                "localField": "_id",

                "foreignField": "emp_code",

                "as": "employee",

            }

        },

        {"$unwind": "$employee"},

    ]



    # Apply the department filter before calculating ranks.

    if department is not None:

        pipeline.append(

            {"$match": {"employee.department": department}}

        )



    # Rank by total lateness. Equal totals receive the same rank.

    pipeline.extend([

        {

            "$setWindowFields": {

                "sortBy": {"total_late_minutes": -1},

                "output": {

                    "rank": {"$rank": {}},

                },

            }

        },

        {"$match": {"rank": {"$lte": limit}}},

        {

            "$sort": {

                "total_late_minutes": -1,

                "_id": 1,

            }

        },

        {

            "$project": {

                "_id": 0,

                "rank": 1,

                "emp_code": "$_id",

                "name": "$employee.name",

                "department": "$employee.department",

                "total_late_minutes": 1,

                "late_count": 1,

            }

        },

    ])



    items = list(db.attendance_logs.aggregate(pipeline))



    return {

        "month": month,

        "items": items,

    }







@app.get("/analytics/departments/{department}/trend")

def department_trend(

    department: str,

    from_date: str = Query(..., alias="from"),

    to_date: str = Query(..., alias="to"),

):

    # 1. Validate the requested date range.

    try:

        start_day = date.fromisoformat(from_date)

        end_day = date.fromisoformat(to_date)

    except (TypeError, ValueError):

        raise HTTPException(

            status_code=422,

            detail="'from' and 'to' must use YYYY-MM-DD format",

        )



    if start_day.isoformat() != from_date or end_day.isoformat() != to_date:

        raise HTTPException(

            status_code=422,

            detail="'from' and 'to' must use YYYY-MM-DD format",

        )



    if end_day < start_day:

        raise HTTPException(

            status_code=422,

            detail="'to' must not be before 'from'",

        )



    days_count = (end_day - start_day).days + 1



    if days_count > 92:

        raise HTTPException(

            status_code=422,

            detail="Date range cannot exceed 92 days",

        )



    # 2. Confirm that the department exists.

    if db.employees.find_one(

        {"department": department},

        {"_id": 1},

    ) is None:

        raise HTTPException(

            status_code=404,

            detail="Department not found",

        )



    # MongoDB stores dates as BSON dates internally in this pipeline.

    start_datetime = datetime.combine(start_day, time.min)



    # 3. Generate every calendar day inside MongoDB.

    # Starting from an employee ensures a seed document exists even

    # when the department has no attendance records in this range.

    pipeline = [

        {

            "$match": {

                "department": department,

            }

        },

        {"$limit": 1},

        {

            "$project": {

                "_id": 0,

                "dates": {

                    "$map": {

                        "input": {"$range": [0, days_count]},

                        "as": "offset",

                        "in": {

                            "$dateAdd": {

                                "startDate": start_datetime,

                                "unit": "day",

                                "amount": "$$offset",

                            }

                        },

                    }

                },

            }

        },

        {"$unwind": "$dates"},

        {

            "$set": {

                "date": "$dates",

                "date_string": {

                    "$dateToString": {

                        "format": "%Y-%m-%d",

                        "date": "$dates",

                        "timezone": "UTC",

                    }

                },

                "is_working_day": {

                    "$not": [

                        {

                            "$in": [

                                {

                                    "$dayOfWeek": "$dates",

                                },

                                [1, 7],

                            ]

                        }

                    ]

                },

            }

        },



        # 4. Find employees in the department who had joined by this day.

        {

            "$lookup": {

                "from": "employees",

                "let": {"day_string": "$date_string"},

                "pipeline": [

                    {

                        "$match": {

                            "department": department,

                            "$expr": {

                                "$lte": ["$joined_on", "$$day_string"]

                            },

                        }

                    },

                    {

                        "$group": {

                            "_id": None,

                            "emp_codes": {"$addToSet": "$emp_code"},

                        }

                    },

                ],

                "as": "employee_data",

            }

        },

        {

            "$set": {

                "employee_codes": {

                    "$ifNull": [

                        {"$arrayElemAt": ["$employee_data.emp_codes", 0]},

                        [],

                    ]

                },

            }

        },

        {

            "$set": {

                "headcount": {"$size": "$employee_codes"},

            }

        },



        # 5. Aggregate attendance for this day and these employees.

        {

            "$lookup": {

                "from": "attendance_logs",

                "let": {

                    "day_string": "$date_string",

                    "codes": "$employee_codes",

                },

                "pipeline": [

                    {

                        "$match": {

                            "$expr": {

                                "$and": [

                                    {"$eq": ["$date", "$$day_string"]},

                                    {"$in": ["$emp_code", "$$codes"]},

                                ]

                            }

                        }

                    },

                    {

                        "$group": {

                            "_id": None,

                            "present_count": {

                                "$sum": {

                                    "$cond": [

                                        {

                                            "$in": [

                                                "$status",

                                                ["PRESENT", "WFH", "ON_DUTY"],

                                            ]

                                        },

                                        {

                                            "$cond": [

                                                {"$eq": ["$half_day", True]},

                                                0.5,

                                                1,

                                            ]

                                        },

                                        0,

                                    ]

                                }

                            },

                            "late_count": {

                                "$sum": {

                                    "$cond": [

                                        {

                                            "$gt": [

                                                {"$ifNull": ["$late_minutes", 0]},

                                                0,

                                            ]

                                        },

                                        1,

                                        0,

                                    ]

                                }

                            },

                        }

                    },

                ],

                "as": "daily_attendance",

            }

        },

        {

            "$set": {

                "present_count": {

                    "$ifNull": [

                        {

                            "$arrayElemAt": [

                                "$daily_attendance.present_count",

                                0,

                            ]

                        },

                        0,

                    ]

                },

                "late_count": {

                    "$ifNull": [

                        {

                            "$arrayElemAt": [

                                "$daily_attendance.late_count",

                                0,

                            ]

                        },

                        0,

                    ]

                },

            }

        },



        # 6. Calculate attendance percentage.

        {

            "$set": {

                "attendance_rate": {

                    "$cond": [

                        {

                            "$and": [

                                "$is_working_day",

                                {"$gt": ["$headcount", 0]},

                            ]

                        },

                        {

                            "$multiply": [

                                {"$divide": ["$present_count", "$headcount"]},

                                100,

                            ]

                        },

                        None,

                    ]

                }

            }

        },



        # 7. Calculate the moving average over the current day and

        # up to six preceding days within the requested range.

        {

            "$setWindowFields": {

                "sortBy": {"date": 1},

                "output": {

                    "moving_avg_7d": {

                        "$avg": "$attendance_rate",

                        "window": {"documents": [-6, 0]},

                    }

                },

            }

        },

        {

            "$project": {

                "_id": 0,

                "date": "$date_string",

                "is_working_day": 1,

                "headcount": 1,

                "present_count": 1,

                "late_count": 1,

                "attendance_rate": 1,

                "moving_avg_7d": 1,

            }

        },

        {"$sort": {"date": 1}},

    ]



    rows = list(db.employees.aggregate(pipeline))



    # 8. Apply decimal half-up rounding for the API response.

    for row in rows:

        for key in ("attendance_rate", "moving_avg_7d"):

            value = row.get(key)



            if value is not None:

                row[key] = float(

                    Decimal(str(value)).quantize(

                        Decimal("0.0001"),

                        rounding=ROUND_HALF_UP,

                    )

                )



        row["present_count"] = float(row["present_count"])



    return {

        "department": department,

        "items": rows,

    }













@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: Literal[
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    ],
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Return MongoDB executionStats for the query/pipeline used by an API endpoint."""

    def explain_find(collection, query, sort=None, skip=0, limit_value=0, projection=None):
        command = {"find": collection.name, "filter": query}
        if projection:
            command["projection"] = projection
        if sort:
            command["sort"] = {key: value for key, value in sort}
        if skip:
            command["skip"] = skip
        if limit_value:
            command["limit"] = limit_value
        return db.command("explain", command, verbosity="executionStats")

    def explain_aggregate(collection, pipeline):
        return db.command(
            "explain",
            {"aggregate": collection.name, "pipeline": pipeline, "cursor": {}},
            verbosity="executionStats",
        )

    def output(payload):
        # json_util safely serializes MongoDB BSON types such as Timestamp/ObjectId.
        return Response(content=json_util.dumps(payload), media_type="application/json")

    def get_month_range(value):
        if not value:
            raise HTTPException(status_code=422, detail="month is required for this endpoint")
        try:
            month_start = datetime.strptime(value, "%Y-%m").date()
        except ValueError:
            raise HTTPException(status_code=422, detail="month must use YYYY-MM format")
        if month_start.strftime("%Y-%m") != value:
            raise HTTPException(status_code=422, detail="month must use YYYY-MM format")
        if month_start.month == 12:
            next_month = date(month_start.year + 1, 1, 1)
        else:
            next_month = date(month_start.year, month_start.month + 1, 1)
        return month_start.isoformat(), (next_month - timedelta(days=1)).isoformat()

    if endpoint == "attendance_list":
        query = {}
        if emp_code:
            query["emp_code"] = emp_code
        if date_from or date_to:
            date_filter = {}
            if date_from:
                date_filter["$gte"] = date_from
            if date_to:
                date_filter["$lte"] = date_to
            query["date"] = date_filter
        if status:
            query["status"] = status
        result = explain_find(
            db.attendance_logs,
            query,
            sort=[("date", -1), ("emp_code", 1)],
            skip=(page - 1) * page_size,
            limit_value=page_size,
        )
        return output({"endpoint": endpoint, "collection": db.attendance_logs.name, "explain": result})

    if endpoint == "employee_monthly":
        if not emp_code:
            raise HTTPException(status_code=422, detail="emp_code is required")
        first_date, last_date = get_month_range(month)
        employee = db.employees.find_one(
            {"emp_code": emp_code}, {"_id": 0, "emp_code": 1, "joined_on": 1}
        )
        if not employee:
            raise HTTPException(status_code=404, detail="Employee not found")
        first_date = max(first_date, employee.get("joined_on", first_date))
        pipeline = [
            {"$match": {"emp_code": emp_code, "date": {"$gte": first_date, "$lte": last_date}}},
            {"$set": {
                "_weekday": {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}},
                "_present_days": {"$cond": [
                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                    {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]}, 0
                ]},
            }},
            {"$group": {
                "_id": None,
                "present_days": {"$sum": {"$cond": [{"$in": ["$_weekday", [2, 3, 4, 5, 6]]}, "$_present_days", 0]}},
                "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
                "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
                "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
            }},
        ]
        result = explain_aggregate(db.attendance_logs, pipeline)
        return output({"endpoint": endpoint, "collection": db.attendance_logs.name, "explain": result})

    if endpoint == "department_summary":
        first_date, last_date = get_month_range(month)
        employee_query = {"joined_on": {"$lte": last_date}}
        if department:
            employee_query["department"] = department
        employee_projection = {"_id": 0, "emp_code": 1, "department": 1, "joined_on": 1}
        employee_explain = explain_find(
            db.employees, employee_query,
            sort=[("department", 1), ("joined_on", 1)],
            projection=employee_projection,
        )
        employees = list(db.employees.find(employee_query, employee_projection).sort([("department", 1), ("joined_on", 1)]))
        codes_by_department = {}
        for employee in employees:
            codes_by_department.setdefault(employee.get("department", "Unknown"), []).append(employee["emp_code"])
        plans = [{"purpose": "employees_for_summary", "collection": db.employees.name, "explain": employee_explain}]
        attendance_projection = {
            "_id": 0, "emp_code": 1, "date": 1, "status": 1, "half_day": 1,
            "work_hours": 1, "late_minutes": 1, "overtime_minutes": 1,
        }
        for dept_name, codes in codes_by_department.items():
            attendance_query = {
                "emp_code": {"$in": codes},
                "date": {"$gte": first_date, "$lte": last_date},
            }
            plans.append({
                "purpose": "attendance_for_department",
                "department": dept_name,
                "collection": db.attendance_logs.name,
                "explain": explain_find(db.attendance_logs, attendance_query, projection=attendance_projection),
            })
        return output({"endpoint": endpoint, "month": month, "plans": plans})

    if endpoint == "late_leaderboard":
        first_date, last_date = get_month_range(month)
        pipeline = [
            {"$match": {"date": {"$gte": first_date, "$lte": last_date}, "late_minutes": {"$gt": 0}}},
            {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": "$late_minutes"}, "late_count": {"$sum": 1}}},
            {"$match": {"total_late_minutes": {"$gt": 0}}},
            {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}},
            {"$unwind": "$employee"},
        ]
        if department:
            pipeline.append({"$match": {"employee.department": department}})
        pipeline.extend([
            {"$setWindowFields": {"sortBy": {"total_late_minutes": -1}, "output": {"rank": {"$rank": {}}}}},
            {"$match": {"rank": {"$lte": limit}}},
            {"$sort": {"total_late_minutes": -1, "_id": 1}},
            {"$project": {
                "_id": 0, "rank": 1, "emp_code": "$_id", "name": "$employee.name",
                "department": "$employee.department", "total_late_minutes": 1, "late_count": 1,
            }},
        ])
        result = explain_aggregate(db.attendance_logs, pipeline)
        return output({"endpoint": endpoint, "collection": db.attendance_logs.name, "explain": result})

    if endpoint == "department_trend":
        if not department:
            raise HTTPException(status_code=422, detail="department is required")
        if not from_date or not to_date:
            raise HTTPException(status_code=422, detail="'from' and 'to' are required")
        try:
            start_day = date.fromisoformat(from_date)
            end_day = date.fromisoformat(to_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="'from' and 'to' must use YYYY-MM-DD format")
        if start_day.isoformat() != from_date or end_day.isoformat() != to_date or end_day < start_day:
            raise HTTPException(status_code=422, detail="Invalid date range")
        days_count = (end_day - start_day).days + 1
        if days_count > 92:
            raise HTTPException(status_code=422, detail="Date range cannot exceed 92 days")
        start_datetime = datetime.combine(start_day, time.min, tzinfo=timezone.utc)
        pipeline = [
            {"$match": {"department": department}},
            {"$limit": 1},
            {"$project": {"_id": 0, "dates": {"$map": {
                "input": {"$range": [0, days_count]}, "as": "offset",
                "in": {"$dateAdd": {"startDate": start_datetime, "unit": "day", "amount": "$$offset"}},
            }}}},
            {"$unwind": "$dates"},
            {"$set": {
                "date": "$dates",
                "date_string": {"$dateToString": {"format": "%Y-%m-%d", "date": "$dates", "timezone": "UTC"}},
                "is_working_day": {"$not": [{"$in": [{"$dayOfWeek": "$dates"}, [1, 7]]}]},
            }},
            {"$lookup": {"from": "employees", "let": {"day_string": "$date_string"}, "pipeline": [
                {"$match": {"department": department, "$expr": {"$lte": ["$joined_on", "$$day_string"]}}},
                {"$group": {"_id": None, "emp_codes": {"$addToSet": "$emp_code"}}},
            ], "as": "employee_data"}},
            {"$set": {"employee_codes": {"$ifNull": [{"$arrayElemAt": ["$employee_data.emp_codes", 0]}, []]}}},
            {"$set": {"headcount": {"$size": "$employee_codes"}}},
            {"$lookup": {"from": "attendance_logs", "let": {"day_string": "$date_string", "codes": "$employee_codes"}, "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$date", "$$day_string"]}, {"$in": ["$emp_code", "$$codes"]}
                ]}}},
                {"$group": {"_id": None,
                    "present_count": {"$sum": {"$cond": [
                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]}, 0
                    ]}},
                    "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
                }},
            ], "as": "daily_attendance"}},
            {"$set": {
                "present_count": {"$ifNull": [{"$arrayElemAt": ["$daily_attendance.present_count", 0]}, 0]},
                "late_count": {"$ifNull": [{"$arrayElemAt": ["$daily_attendance.late_count", 0]}, 0]},
            }},
            {"$set": {"attendance_rate": {"$cond": [
                {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                {"$multiply": [{"$divide": ["$present_count", "$headcount"]}, 100]}, None
            ]}}},
            {"$setWindowFields": {"sortBy": {"date": 1}, "output": {"moving_avg_7d": {"$avg": "$attendance_rate", "window": {"documents": [-6, 0]}}}}},
            {"$project": {"_id": 0, "date": "$date_string", "is_working_day": 1, "headcount": 1,
                          "present_count": 1, "late_count": 1, "attendance_rate": 1, "moving_avg_7d": 1}},
            {"$sort": {"date": 1}},
        ]
        result = explain_aggregate(db.employees, pipeline)
        return output({"endpoint": endpoint, "collection": db.employees.name, "explain": result})

    raise HTTPException(status_code=422, detail="Unsupported endpoint")



# TODO - the rest of the contract (see openapi.yaml):

#   POST  /attendance/punch-out

#   PATCH /attendance/{emp_code}/{date}

#   GET   /analytics/employees/{emp_code}/monthly

#   GET   /analytics/departments/summary

#   GET   /analytics/leaderboard/late

#   GET   /analytics/departments/{department}/trend

#   GET   /admin/explain/{endpoint}

# --------------------------------------------------------------------------- #
