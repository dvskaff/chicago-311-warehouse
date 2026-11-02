{#- Small generic tests written in-house so the project needs no external dbt packages. -#}

{% test not_negative(model, column_name) %}
select {{ column_name }} from {{ model }} where {{ column_name }} < 0
{% endtest %}

{% test between(model, column_name, min_value, max_value) %}
select {{ column_name }} from {{ model }}
where {{ column_name }} < {{ min_value }} or {{ column_name }} > {{ max_value }}
{% endtest %}

{% test expression_is_true(model, expression, column_name=None) %}
select * from {{ model }} where not ({{ expression }})
{% endtest %}

{% test unique_combination(model, combination_of_columns) %}
select {{ combination_of_columns | join(', ') }}, count(*) as n
from {{ model }}
group by {{ combination_of_columns | join(', ') }}
having count(*) > 1
{% endtest %}
