import time
import uuid
import base64
from collections import defaultdict
from typing import Optional, List, Dict
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

# Assigned values
TOTAL_ORDERS = 59
RATE_LIMIT_R = 20
RATE_LIMIT_WINDOW = 10.0  # seconds

# Enable CORS for browser verification
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Catalog Setup (Fixed IDs 1 through T)
CATALOG: List[Dict] = [
    {"id": i, "item": f"Order #{i}", "created_at": "2026-10-18T20:00:00Z"}
    for i in range(1, TOTAL_ORDERS + 1)
]

IDEMPOTENCY_STORE: Dict[str, Dict] = {}
CLIENT_REQUESTS: Dict[str, List[float]] = defaultdict(list)

# Rate Limiting Middleware (Bypasses OPTIONS preflight)
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Allow CORS preflight OPTIONS requests to pass through immediately
    if request.method == "OPTIONS":
        return await call_next(request)

    client_id = request.headers.get("X-Client-Id")
    if client_id and request.url.path.startswith("/orders"):
        now = time.time()
        timestamps = CLIENT_REQUESTS[client_id]
        
        valid_timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
        CLIENT_REQUESTS[client_id] = valid_timestamps
        
        if len(valid_timestamps) >= RATE_LIMIT_R:
            oldest_in_window = valid_timestamps[0]
            retry_after = int(RATE_LIMIT_WINDOW - (now - oldest_in_window)) + 1
            return Response(
                content='{"detail": "Rate limit exceeded"}',
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Retry-After": str(max(1, retry_after))},
                media_type="application/json",
            )
        
        CLIENT_REQUESTS[client_id].append(now)

    response = await call_next(request)
    return response

# Idempotent Order Creation
@app.post("/orders", status_code=status.HTTP_201_CREATED)
def create_order(
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
):
    if idempotency_key:
        if idempotency_key in IDEMPOTENCY_STORE:
            response.status_code = status.HTTP_201_CREATED
            return IDEMPOTENCY_STORE[idempotency_key]

        order_id = str(uuid.uuid4())
        res_data = {"id": order_id, "status": "created"}
        IDEMPOTENCY_STORE[idempotency_key] = res_data
        return res_data
    
    return {"id": str(uuid.uuid4()), "status": "created"}

# Cursor-Based Pagination
def encode_cursor(offset: int) -> str:
    return base64.b64encode(str(offset).encode("utf-8")).decode("utf-8")

def decode_cursor(cursor_str: str) -> int:
    try:
        decoded = base64.b64decode(cursor_str.encode("utf-8")).decode("utf-8")
        return int(decoded)
    except Exception:
        return 0

@app.get("/orders")
def get_orders(
    limit: int = Query(10, ge=1, le=100),
    cursor: Optional[str] = Query(None)
):
    start_index = decode_cursor(cursor) if cursor else 0
    end_index = start_index + limit
    
    page_items = CATALOG[start_index:end_index]
    
    next_cursor = None
    if end_index < len(CATALOG):
        next_cursor = encode_cursor(end_index)

    return {
        "items": page_items,
        "next_cursor": next_cursor
    }
