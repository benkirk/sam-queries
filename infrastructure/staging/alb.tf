resource "aws_alb" "main" {
  name               = "${local.name_prefix}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  tags = { Name = "${local.name_prefix}-alb" }
}

resource "aws_alb_target_group" "webapp" {
  name        = "${local.name_prefix}-webapp-tg"
  port        = var.container_port
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    enabled             = true
    path                = "/api/v1/health/"
    port                = "traffic-port"
    protocol            = "HTTP"
    healthy_threshold   = 3
    unhealthy_threshold = 3
    timeout             = 10
    interval            = 30
    matcher             = "200"
  }

  tags = { Name = "${local.name_prefix}-webapp-tg" }
}

# HTTP listener (upgrade to HTTPS when ACM cert is ready)
resource "aws_alb_listener" "http" {
  load_balancer_arn = aws_alb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_alb_target_group.webapp.arn
  }
}

# HTTPS listener — required before enabling AUTH_PROVIDER=oidc
# Uncomment when ACM certificate and DNS are ready:
#
# resource "aws_alb_listener" "https" {
#   load_balancer_arn = aws_alb.main.arn
#   port              = 443
#   protocol          = "HTTPS"
#   ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
#   certificate_arn   = aws_acm_certificate.webapp.arn
#
#   default_action {
#     type             = "forward"
#     target_group_arn = aws_alb_target_group.webapp.arn
#   }
# }
