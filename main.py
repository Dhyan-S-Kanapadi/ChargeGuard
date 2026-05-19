from fastapi import FastAPI


app = FastAPI(title="ChargeGuard AI")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
