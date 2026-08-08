-- Postgres has TWO independent RLS bypasses, and migration 006 closes only one of them.
--
--   1. The TABLE OWNER bypasses RLS unless FORCE ROW LEVEL SECURITY is set. 006 sets FORCE,
--      so this one is closed.
--   2. A SUPERUSER, or any role holding BYPASSRLS, skips policy evaluation entirely.
--      FORCE has no effect on this whatsoever - check_enable_rls short-circuits before
--      policies are ever consulted.
--
-- The Postgres container's POSTGRES_USER is the cluster's bootstrap superuser and holds both
-- attributes. Verified directly against a live container: with only 001-006 applied, a
-- tenant-scoped SELECT returns every tenant's rows and a cross-tenant INSERT succeeds. The
-- bootstrap superuser also cannot be demoted - Postgres refuses with "bootstrap superuser
-- must have the SUPERUSER attribute" - so the fix has to be a second role.
--
-- releaselens_app is NOLOGIN on purpose: nothing ever connects as it, so there is no second
-- credential to create, store, rotate or leak. TenantConnectionFactory issues SET LOCAL ROLE
-- inside its transaction, which makes current_user a role that cannot bypass RLS for that
-- transaction only. Postgres restores the previous role on commit or rollback, so a pooled
-- connection cannot carry it into its next borrower.
--
-- This also makes local behaviour match Azure. Azure Database for PostgreSQL Flexible Server's
-- admin is not a true superuser and does not hold BYPASSRLS, so FORCE alone would enforce
-- correctly in Azure while silently failing on every developer machine - the worst kind of
-- divergence, because the tests would pass where it does not matter and the gap would only
-- exist where it does.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'releaselens_app') then
        create role releaselens_app nologin;
    end if;
end $$;

grant usage on schema public to releaselens_app;
grant select, insert, update, delete on all tables in schema public to releaselens_app;
grant usage, select on all sequences in schema public to releaselens_app;

-- Tables added by later migrations must not silently land without app-role access.
alter default privileges in schema public
    grant select, insert, update, delete on tables to releaselens_app;
alter default privileges in schema public
    grant usage, select on sequences to releaselens_app;
