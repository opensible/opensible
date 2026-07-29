# Sample values. The wizard overwrites this file on every save.
env          = "dev"
project_name = "opensible-demo"

account_id = "REPLACE_ME_ACCOUNT_ID"
zone_name  = "example.com"

create_zone = false

dns_records = [
  { name = "@",   type = "A",     content = "192.0.2.10", ttl = 1, proxied = true },
  { name = "www", type = "CNAME", content = "@",          ttl = 1, proxied = true },
]

r2_buckets    = []
workers       = []
worker_routes = []
access_apps   = []
