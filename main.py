import time
import base64
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, Header, Query, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from collections import defaultdict, deque

# ============================================================================
# SETUP
# ============================================================================
app = FastAPI(title="Orders API")

# Allow cross-origin requests (CORS) so the grader's browser can call your API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Your assigned values
TOTAL_ORDERS = 47
RATE_LIMIT_REQUESTS = 17
RATE_LIMIT_WINDOW_SECONDS = 10

# ============================================================================
# PART 1: IDEMPOTENT ORDER CREATION
# ============================================================================

# Store orders here: key = idempotency_key, value = {"id": "...", "created_at": "..."}
orders_db = {}

class OrderResponse(BaseModel):
    id: str
    created_at: str

@app.post("/orders", status_code=201)
def create_order(Idempotency_Key: str = Header(...)):
    """
    POST /orders with Idempotency-Key header.
    
    On first call: create order, return HTTP 201
    On repeat call with same key: return same order (HTTP 200)
    
    The Idempotency-Key header prevents duplicate orders if the same
    request is sent multiple times.
    """
    
    # Check if we've seen this key before
    if Idempotency_Key in orders_db:
        # Return existing order (but still with 201 for idempotent POST)
        order = orders_db[Idempotency_Key]
        return JSONResponse(
            status_code=201,
            content={"id": order["id"], "created_at": order["created_at"]}
        )
    
    # New order: create it
    order_id = f"order-{len(orders_db) + 1}"
    created_at = datetime.utcnow().isoformat()
    
    orders_db[Idempotency_Key] = {
        "id": order_id,
        "created_at": created_at
    }
    
    return {
        "id": order_id,
        "created_at": created_at
    }


# ============================================================================
# PART 2: CURSOR-BASED PAGINATION
# ============================================================================

class PaginatedOrdersResponse(BaseModel):
    items: list
    next_cursor: Optional[str] = None

@app.get("/orders")
def list_orders(
    limit: int = Query(10, ge=1, le=TOTAL_ORDERS),
    cursor: Optional[str] = Query(None)
):
    """
    GET /orders?limit=10&cursor=abc123
    
    Returns up to `limit` orders from a fixed catalog of order IDs 1..47.
    
    The cursor is opaque (you don't need to understand it), but it tells
    the API where to start in the list. The API returns a new cursor
    for fetching the next page.
    
    This ensures no gaps, no repeats, and no over-sized pages.
    """
    
    # Decode cursor to get starting position
    if cursor is None:
        start_id = 1
    else:
        try:
            # Cursor is base64-encoded position (e.g., "start:15")
            decoded = base64.b64decode(cursor).decode("utf-8")
            start_id = int(decoded.split(":")[1])
        except:
            raise HTTPException(status_code=400, detail="Invalid cursor")
    
    # Validate that cursor isn't beyond our range
    if start_id > TOTAL_ORDERS:
        start_id = TOTAL_ORDERS
    
    # Build the items for this page
    items = []
    end_id = min(start_id + limit - 1, TOTAL_ORDERS)
    
    for order_id in range(start_id, end_id + 1):
        items.append({
            "id": order_id,
            "name": f"Order #{order_id}",
            "amount": 100.0 + order_id
        })
    
    # Calculate next cursor (if there are more items)
    next_cursor = None
    if end_id < TOTAL_ORDERS:
        next_pos = end_id + 1
        cursor_str = f"start:{next_pos}"
        next_cursor = base64.b64encode(cursor_str.encode()).decode()
    
    return {
        "items": items,
        "next_cursor": next_cursor
    }


# ============================================================================
# PART 3: PER-CLIENT RATE LIMITING
# ============================================================================

# Track requests per client: {client_id: deque of timestamps}
client_requests = defaultdict(deque)

@app.middleware("http")
async def rate_limit_middleware(request, call_next):
    """
    Rate limiting middleware that runs on EVERY request.
    
    1. Read X-Client-Id header
    2. Check if this client has made >= 17 requests in the last 10 seconds
    3. If 17+ requests exist, block (HTTP 429)
    4. Otherwise, record request and allow through
    """
    
    # Get client ID from header
    client_id = request.headers.get("X-Client-Id")
    
    # If no client ID provided, skip rate limiting
    if not client_id:
        response = await call_next(request)
        return response
    
    current_time = time.time()
    
    # Remove old requests (older than 10 seconds)
    # ⚠️ IMPORTANT: Do this BEFORE checking the limit
    while client_requests[client_id] and (current_time - client_requests[client_id][0] > RATE_LIMIT_WINDOW_SECONDS):
        client_requests[client_id].popleft()
    
    # Count valid requests (within 10-second window)
    request_count = len(client_requests[client_id])
    
    # Block if already at or exceeding limit
    if request_count >= RATE_LIMIT_REQUESTS:
        # Calculate Retry-After: how long until oldest request expires
        oldest_request_time = client_requests[client_id][0]
        time_since_oldest = current_time - oldest_request_time
        retry_after_seconds = RATE_LIMIT_WINDOW_SECONDS - time_since_oldest
        retry_after_seconds = max(1, int(retry_after_seconds) + 1)
        
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Rate limit exceeded",
                "limit": RATE_LIMIT_REQUESTS,
                "window_seconds": RATE_LIMIT_WINDOW_SECONDS,
                "current_requests": request_count
            },
            headers={"Retry-After": str(retry_after_seconds)}
        )
    
    # Record this request BEFORE calling the endpoint
    client_requests[client_id].append(current_time)
    
    # Continue to the actual endpoint
    response = await call_next(request)
    
    # Add rate limit info to response headers
    remaining = RATE_LIMIT_REQUESTS - len(client_requests[client_id])
    response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_REQUESTS)
    response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
    response.headers["X-RateLimit-Window"] = str(RATE_LIMIT_WINDOW_SECONDS)
    
    return response


# ============================================================================
# HEALTH CHECK (for testing)
# ============================================================================

@app.get("/health")
def health_check():
    """Simple health check endpoint"""
    return {"status": "ok"}
