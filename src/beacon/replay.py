from datetime import UTC
from typing import Any

from beacon import upload
from beacon.app import write
from beacon.geo import Location
from beacon.upload import Context


async def replay(client: Any) -> list[str]:
    pending = await client.query(
        "SELECT upload_id, project_id, received_at, ip_country, asn, as_org, geo_database_date"
        " FROM uploads WHERE status = 'schema_newer' AND upload_id NOT IN"
        " (SELECT upload_id FROM uploads WHERE status = 'replayed') ORDER BY received_at"
    )
    report = []
    for upload_id, project_id, received_at, country, asn, as_org, geo_date in pending.result_rows:
        stored = await client.query(
            "SELECT raw FROM rejected_events WHERE upload_id = {id:UUID}"
            " AND reason = 'schema_newer' ORDER BY line",
            parameters={"id": upload_id},
        )
        lines = [row[0].encode() for row in stored.result_rows]
        metadata = upload.read_header(lines[0])
        if upload.is_newer(metadata["schema_version"]):
            report.append(f"{upload_id}: schema {metadata['schema_version']} is still newer")
            continue
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=UTC)
        ctx = Context(
            project_id=project_id,
            upload_id=upload_id,
            received_at=received_at,
            location=Location(country, asn, as_org, geo_date),
        )
        result = upload.process(lines, metadata, ctx)
        result.status = "replayed"
        await write(client, ctx, result)
        report.append(f"{upload_id}: {len(result.events)} events, {result.rejected_count} invalid")
    return report
