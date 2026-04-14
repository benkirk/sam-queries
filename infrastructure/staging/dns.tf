# DNS and TLS configuration for sam-staging.csgsam.ucar.edu
#
# The csgsam.ucar.edu hosted zone is managed by UCAR HelpDesk (sweg-management).
# We create records within it for ALB routing and ACM certificate validation.
# A separate CNAME (sam-staging.ucar.edu -> sam-staging.csgsam.ucar.edu) is
# managed outside this account by UCAR DNS team via Infoblox.

data "aws_route53_zone" "csgsam" {
  name = "csgsam.ucar.edu."
}

# --- ACM Certificate ---

resource "aws_acm_certificate" "webapp" {
  domain_name       = "${local.name_prefix}.csgsam.ucar.edu"
  validation_method = "DNS"

  tags = { Name = "${local.name_prefix}-cert" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.webapp.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      record = dvo.resource_record_value
    }
  }

  zone_id         = data.aws_route53_zone.csgsam.zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "webapp" {
  certificate_arn         = aws_acm_certificate.webapp.arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

# --- ALB DNS Record ---

resource "aws_route53_record" "staging" {
  zone_id = data.aws_route53_zone.csgsam.zone_id
  name    = "${local.name_prefix}.csgsam.ucar.edu"
  type    = "A"

  alias {
    name                   = aws_alb.main.dns_name
    zone_id                = aws_alb.main.zone_id
    evaluate_target_health = true
  }
}
