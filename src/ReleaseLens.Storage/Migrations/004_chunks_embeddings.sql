create table evidence_chunks (
    chunk_id     bigint generated always as identity primary key,
    tenant_id    uuid not null references tenants(tenant_id) on delete cascade,
    entity_type  text not null check (entity_type in ('commit', 'issue', 'pull_request', 'release')),
    entity_key   text not null,
    chunk_index  integer not null,
    content      text not null,
    token_count  integer not null,
    content_tsv  tsvector generated always as (to_tsvector('english', content)) stored,
    created_at   timestamptz not null default now(),
    unique (tenant_id, entity_type, entity_key, chunk_index),
    -- Composite-FK target. Foreign-key checks run with RLS bypassed by design, so a child
    -- referencing chunk_id alone lets a tenant-A scope write a row pointing at a tenant-B
    -- chunk: the FK check ignores RLS and the WITH CHECK policy only sees A's own tenant_id.
    -- Verified against a live container. files_changed already uses this pattern.
    unique (tenant_id, chunk_id)
);

create index evidence_chunks_tsv_idx on evidence_chunks using gin (content_tsv);
create index evidence_chunks_entity_idx on evidence_chunks (tenant_id, entity_type, entity_key);

create table embeddings (
    chunk_id    bigint primary key,
    tenant_id   uuid not null references tenants(tenant_id) on delete cascade,
    model       text not null,
    dim         integer not null,
    embedding   vector(384) not null,
    created_at  timestamptz not null default now(),
    constraint embeddings_dim_matches check (dim = 384),
    -- Tenant-scoped: referencing chunk_id alone would permit a cross-tenant reference.
    foreign key (tenant_id, chunk_id) references evidence_chunks (tenant_id, chunk_id) on delete cascade
);

create index embeddings_hnsw_idx on embeddings
    using hnsw (embedding vector_cosine_ops) with (m = 16, ef_construction = 64);

create index embeddings_tenant_idx on embeddings (tenant_id);

comment on table embeddings is
    'One row per chunk. vector(384) is BGE-small-en-v1.5. Changing model means a new migration widening the column and a full re-embed.';
