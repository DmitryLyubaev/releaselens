using Dapper;
using ReleaseLens.Core.Evidence;

namespace ReleaseLens.Storage.Repositories;

public sealed class CheckpointRepository
{
    public async Task<EvidenceCursor> GetAsync(
        TenantScope scope, EntityType entityType, CancellationToken cancellationToken)
    {
        var row = await scope.Connection.QuerySingleOrDefaultAsync<(string? cursor_value, string? etag)>(
            new CommandDefinition(
                "select cursor_value, etag from ingest_checkpoints where entity_type = @type",
                new { type = entityType.ToWireName() }, scope.Transaction, cancellationToken: cancellationToken));

        return new EvidenceCursor(scope.TenantId, row.cursor_value, row.etag);
    }

    public Task SaveAsync(
        TenantScope scope, EntityType entityType, string? cursorValue, string? etag,
        long itemsSeen, CancellationToken cancellationToken)
        => scope.Connection.ExecuteAsync(new CommandDefinition(
            """
            insert into ingest_checkpoints (tenant_id, entity_type, cursor_value, etag, items_seen, updated_at)
            values (@TenantId, @EntityType, @CursorValue, @ETag, @ItemsSeen, now())
            on conflict (tenant_id, entity_type) do update set
                cursor_value = excluded.cursor_value,
                etag = excluded.etag,
                -- Additive, and therefore the one non-idempotent write in an otherwise fully
                -- upsert-idempotent pipeline: a resumed run that re-presents its last batch
                -- counts those items twice. That is accepted deliberately - this is an operator
                -- progress signal for a multi-hour ingest, not a metric anything reasons from,
                -- and making it exact would mean threading a running total through Task 9 for
                -- no benefit. Read it as "items presented", not "distinct items ingested".
                items_seen = ingest_checkpoints.items_seen + excluded.items_seen,
                updated_at = now()
            """,
            new
            {
                scope.TenantId,
                EntityType = entityType.ToWireName(),
                CursorValue = cursorValue,
                ETag = etag,
                ItemsSeen = itemsSeen
            },
            scope.Transaction, cancellationToken: cancellationToken));
}
