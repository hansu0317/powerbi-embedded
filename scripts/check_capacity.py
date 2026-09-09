"""Read capacity without changing Power BI. Exit 0=supported SKU,
2=unsupported/no capacity, 3=unknown. A supported SKU still needs a paid,
active capacity and a render/load test before production use.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import config
from services.azure import get_access_token


def classify_capacity(workspace: dict, capacities: list[dict]) -> tuple[int, str]:
    if not workspace.get("isOnDedicatedCapacity"):
        return 2, "NO_CAPACITY: production App-Owns-Data requires paid capacity."
    capacity_id = str(workspace.get("capacityId", "")).lower()
    capacity = next((c for c in capacities if str(c.get("id", "")).lower() == capacity_id), None)
    if not capacity:
        return 3, "UNKNOWN: assigned capacity SKU is not visible to this identity."
    sku = str(capacity.get("sku", "")).upper()
    if re.fullmatch(r"(?:F|A|EM|P)[1-9][0-9]*", sku):
        return 0, f"SUPPORTED_SKU: {sku}; verify paid/active status and actual rendering."
    return 2, f"UNSUPPORTED_SKU: {sku}; PPU/PP3 does not meet production App-Owns-Data licensing."


def main() -> int:
    try:
        headers = {"Authorization": f"Bearer {get_access_token()}"}
        with httpx.Client(timeout=30) as client:
            ws = client.get(f"{config.PBI_GROUPS}/{config.WORKSPACE_ID}", headers=headers)
            print(f"workspace_http={ws.status_code}")
            if ws.status_code != 200:
                return 3
            workspace = ws.json()
            caps = client.get("https://api.powerbi.com/v1.0/myorg/capacities", headers=headers)
            print(f"capacities_http={caps.status_code}")
            print(f"dedicated={workspace.get('isOnDedicatedCapacity')}")
            code, message = classify_capacity(workspace, caps.json().get("value", []) if caps.status_code == 200 else [])
            print(message)
            print("Reference: https://learn.microsoft.com/en-us/power-bi/guidance/powerbi-implementation-planning-usage-scenario-embed-for-your-customers#licensing")
            return code
    except Exception as exc:
        print(f"CHECK_FAILED: {type(exc).__name__}; check network and server credentials.")
        return 3


if __name__ == "__main__":
    sys.exit(main())
