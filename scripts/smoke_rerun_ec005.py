import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api"


def get(path: str):
    with urllib.request.urlopen(BASE + path) as response:
        return json.loads(response.read().decode())


def post(path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode())


def main() -> None:
    try:
        get("/cases/EC_005")
    except urllib.error.HTTPError:
        created = post(
            "/cases",
            {
                "order_id": "e2e8f3a59d7067fe62c900692f5fdcaf",
                "customer_message": "Late delivery claim",
                "language": "vi",
            },
        )
        print("created", created.get("case_id"))

    run = post("/cases/EC_005/runs")
    print("started", run)
    for index in range(50):
        time.sleep(4)
        detail = get("/cases/EC_005")
        latest = detail["runs"][0]
        print(index, latest["status"], (latest.get("error_message") or "")[:120])
        if latest["status"] in {"completed", "failed"}:
            output = latest.get("final_output")
            if output:
                print(
                    "issue",
                    output["assessment"]["primary_issue"],
                    "refund",
                    output["financial_resolution"]["recommended_refund_brl"],
                )
            break


if __name__ == "__main__":
    main()
