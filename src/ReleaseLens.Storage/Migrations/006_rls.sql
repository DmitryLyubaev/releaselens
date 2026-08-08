alter table commits              enable row level security;
alter table files_changed        enable row level security;
alter table issues               enable row level security;
alter table pull_requests        enable row level security;
alter table releases             enable row level security;
alter table evidence_chunks      enable row level security;
alter table embeddings           enable row level security;
alter table ingest_checkpoints   enable row level security;
alter table embedding_dead_letter enable row level security;
alter table token_usage          enable row level security;

alter table commits              force row level security;
alter table files_changed        force row level security;
alter table issues               force row level security;
alter table pull_requests        force row level security;
alter table releases             force row level security;
alter table evidence_chunks      force row level security;
alter table embeddings           force row level security;
alter table ingest_checkpoints   force row level security;
alter table embedding_dead_letter force row level security;
alter table token_usage          force row level security;

create or replace function releaselens_current_tenant() returns uuid
language sql stable as $$
    select nullif(current_setting('releaselens.tenant_id', true), '')::uuid
$$;

do $$
declare
    t text;
begin
    foreach t in array array[
        'commits', 'files_changed', 'issues', 'pull_requests', 'releases',
        'evidence_chunks', 'embeddings', 'ingest_checkpoints',
        'embedding_dead_letter', 'token_usage'
    ]
    loop
        execute format(
            'create policy %I_tenant_isolation on %I using (tenant_id = releaselens_current_tenant()) with check (tenant_id = releaselens_current_tenant())',
            t, t);
    end loop;
end $$;
