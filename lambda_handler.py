from mangum import Mangum

from app.main import app

# Works with API Gateway HTTP API and Lambda Function URLs (what CloudFront will call).
handler = Mangum(app, lifespan="off")
