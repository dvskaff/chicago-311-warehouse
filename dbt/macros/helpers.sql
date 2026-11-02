{#- Surrogate key: md5 of the natural key, the same pattern used on most warehouses. -#}
{% macro surrogate_key(expr) -%}
    md5(coalesce(cast({{ expr }} as varchar), '_null_'))
{%- endmacro %}

{#- Clean free-text categories coming from the source: trim, collapse blanks to NULL. -#}
{% macro clean_text(col) -%}
    nullif(trim(regexp_replace({{ col }}, '\s+', ' ', 'g')), '')
{%- endmacro %}
