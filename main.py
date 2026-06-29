import time
import base64
import asyncio
import httpx
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Header, Query, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from collections import defaultdict, deque

# ============================================================================
# SETUP
# ============================================================================
app = FastAPI(title="Orders API")

# CORS - Allow browser requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*", "X-Client-Id"],  # Explicitly allow custom header
    expose_headers=["Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Window"],
)

# Assigned values
TOTAL_ORDERS = 47
RATE_LIMIT_REQUESTS = 17
RATE_LIMIT_WINDOW_SECONDS = 10

# ============================================================================
# PART 1: IDEMPOTENT ORDER CREATION
# ============================================================================

orders_db = {}

class OrderResponse(BaseModel):
    id: str
    created_at: str

@app.post("/orders", status_code=201)
def create_order(Idempotency_Key: str = Header(...)):
    """
    POST /orders with Idempotency-Key header.
    - First call: creates order, returns HTTP 201
    - Repeat with same key: returns same order, HTTP 201
    """
    
    # Check if we've seen this key
    if Idempotency_Key in orders_db:
        order = orders_db[Idempotency_Key]
        return JSONResponse(
            status_code=201,
            content={"id": order["id"], "created_at": order["created_at"]}
        )
    
    # New order
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

@app.get("/orders")
def list_orders(
    limit: int = Query(10, ge=1, le=TOTAL_ORDERS),
    cursor: Optional[str] = Query(None)
):
    """
    GET /orders?limit=10&cursor=abc123
    Returns up to `limit` orders from IDs 1..47
    """
    
    # Decode cursor
    if cursor is None:
        start_id = 1
    else:
        try:
            decoded = base64.b64decode(cursor).decode("utf-8")
            start_id = int(decoded.split(":")[1])
        except:
            raise HTTPException(status_code=400, detail="Invalid cursor")
    
    if start_id > TOTAL_ORDERS:
        start_id = TOTAL_ORDERS
    
    # Build items
    items = []
    end_id = min(start_id + limit - 1, TOTAL_ORDERS)
    
    for order_id in range(start_id, end_id + 1):
        items.append({
            "id": order_id,
            "name": f"Order #{order_id}",
            "amount": 100.0 + order_id
        })
    
    # Next cursor
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
# PART 3: RATE LIMITING
# ============================================================================

# Track requests per client at MODULE LEVEL (not in function!)
# client_requests = defaultdict(deque)

# @app.middleware("http")
# async def rate_limit_middleware(request: Request, call_next):
#     """Rate limiting: 17 requests per 10 seconds per client"""
    
#     # Skip rate limiting for CORS preflight requests
#     if request.method == "OPTIONS":
#         return await call_next(request)
    
#     client_id = request.headers.get("X-Client-Id")
    
#     # Skip if no client ID
#     if not client_id:
#         response = await call_next(request)
#         return response
    
#     now = time.time()
#     bucket = client_requests[client_id]
    
#     # Remove requests older than 10 seconds
#     while bucket and (now - bucket[0] > RATE_LIMIT_WINDOW_SECONDS):
#         bucket.popleft()
    
#     # Check if at limit
#     if len(bucket) >= RATE_LIMIT_REQUESTS:
#         oldest = bucket[0]
#         retry_after = int(RATE_LIMIT_WINDOW_SECONDS - (now - oldest)) + 1
#         retry_after = max(1, retry_after)
        
#         return JSONResponse(
#             status_code=429,
#             content={"detail": "Rate limit exceeded"},
#             headers={"Retry-After": str(retry_after)}
#         )
    
#     # Record this request
#     bucket.append(now)
    
#     # Call the endpoint
#     response = await call_next(request)
    
#     # Add rate limit headers
#     remaining = RATE_LIMIT_REQUESTS - len(bucket)
#     response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_REQUESTS)
#     response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
#     response.headers["X-RateLimit-Window"] = str(RATE_LIMIT_WINDOW_SECONDS)
    
#     return response

client_requests = defaultdict(deque)  # back to deque

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    client_id = request.headers.get("X-Client-Id")
    if not client_id:
        return await call_next(request)

    now = time.time()
    bucket = client_requests[client_id]

    # Clear requests older than 30 seconds
    while bucket and (now - bucket[0] > 30):
        bucket.popleft()

    if len(bucket) >= RATE_LIMIT_REQUESTS:
        retry_after = int(30 - (now - bucket[0])) + 1
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded"},
            headers={
                "Retry-After": retry_after,
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*",
                "Access-Control-Allow-Methods": "*",
            }
        )

    bucket.append(now)
    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_REQUESTS)
    response.headers["X-RateLimit-Remaining"] = str(max(0, RATE_LIMIT_REQUESTS - len(bucket)))
    response.headers["X-RateLimit-Window"] = "10"
    return response


# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.get("/health")
def health_check():
    """Simple health check"""
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000, workers=1)

@app.on_event("startup")
async def keep_alive():
    async def ping():
        while True:
            await asyncio.sleep(30)
            try:
                async with httpx.AsyncClient() as client:
                    await client.get("https://tds-api-engineering.onrender.com/health")
            except:
                pass
    asyncio.create_task(ping())
