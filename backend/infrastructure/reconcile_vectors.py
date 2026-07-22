from __future__ import annotations

from backend.application.dependencies import initialize_application_foundation
from backend.application.vector_outbox import reconcile_pending_vectors


def main() -> None:
    initialize_application_foundation()
    result = reconcile_pending_vectors()
    print(
        "Vector reconciliation: "
        f"attempted={result.attempted}, completed={result.completed}, "
        f"failed={result.failed}"
    )
    if result.failed_job_ids:
        print("Failed job IDs: " + ", ".join(result.failed_job_ids))


if __name__ == "__main__":
    main()
