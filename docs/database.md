# Observed database

Supplied image digest: sha256:714ff812e4d1ceb832ac41bc1a7d471cd5485fe14cb7a01b705909933a11097d.
The dataset has 89397 jobs, 405050 technology relationships, 80811 function
relationships, 9413 technologies, 170 functions, and five organizations.
No indexes or declared constraints were observed.

job_posts_details.id links to job_posts.id; organization_id links to
organizations.id. job_posts_tech uses job_post_id and tech_id;
job_posts_job_functions uses job_post_id and job_function_id. Dictionary
names live in tech, job_functions, and organizations. Ignore denormalized
text association columns. All observed entity IDs are unique bigint values.

This inspection is evidence for the approved architecture, not a migration.
Real SQL acceptance tests and schema documentation will be completed later.
