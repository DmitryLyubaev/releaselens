create table tenants (
    tenant_id           uuid primary key,
    slug                text not null unique,
    display_name        text not null,
    source_name         text not null,
    repo_owner          text not null,
    repo_name           text not null,
    daily_token_budget  bigint not null default 1000000,
    created_at          timestamptz not null default now(),
    constraint tenants_slug_format check (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$')
);

create table api_keys (
    api_key_id  uuid primary key,
    tenant_id   uuid not null references tenants(tenant_id) on delete cascade,
    key_hash    bytea not null unique,
    label       text not null,
    created_at  timestamptz not null default now(),
    revoked_at  timestamptz
);

create index api_keys_tenant_idx on api_keys (tenant_id) where revoked_at is null;

comment on column api_keys.key_hash is
    'SHA-256 of the presented key. The key itself is shown once at creation and never stored.';
