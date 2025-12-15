# runbook_data.py

from typing import List, Dict

# A list of initial troubleshooting articles/documents.
# In a real-world scenario, this data would likely be loaded from
# a persistent database, configuration files, or a wiki.

INITIAL_RUNBOOK_ENTRIES: List[Dict[str, str]] = [
    {
        "issue": "Microservice A is down, users see a 503 error on login.",
        "solution": "1. Check the logs for Microservice A on the main cluster. 2. If 'Out of Memory' error is present, restart the Kubernetes pod for Microservice A (kubectl rollout restart deployment microservice-a). 3. Verify user login works."
    },
    {
        "issue": "Database connection pooling issue, slow report generation.",
        "solution": "1. Check the database connection pool size metric. 2. If pool utilization is over 90%, double the pool size setting in the application configuration (DB_POOL_SIZE) and redeploy the affected service."
    },
    {
        "issue": "API endpoint /user/status returns a 404, though it existed before.",
        "solution": "1. Verify the service routing configuration (Ingress/Gateway). 2. Check recent deployment history for the User Service for any changes to API paths. 3. Rollback the latest deployment if an API path change is suspected."
    },
    {
        "issue": "Disk space utilization on the primary data node is high (over 95%).",
        "solution": "1. Run the cleanup script `/opt/scripts/log_cleanup.sh` to remove old archives. 2. If space is still low, alert the infrastructure team for potential volume expansion."
    },
    {
    "issue": "Daily Synchronization Job Failing with Java NullPointer Exception",
    "solution": "To resolve the Java NullPointer Exception in the transform service, first check the configuration files and logs for any missing or null values. Then, verify that all dependencies and libraries are properly loaded and initialized. If the issue persists, review the code for any potential null pointer dereferences and update the code to handle null values. Additionally, consider increasing the logging level to debug to gather more detailed information about the exception."
},
    {
    "issue": "The payment service is experiencing slowness with RedisTimeoutException when connecting to the cache, which is unrelated to the current database connection pooling issue.",
    "solution": "Investigate Redis connection settings, check for any network issues between the service and Redis, and consider increasing the Redis connection timeout or optimizing Redis queries."
}
]