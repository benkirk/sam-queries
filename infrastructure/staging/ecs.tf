resource "aws_ecs_cluster" "main" {
  name = local.name_prefix

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = { Name = local.name_prefix }
}

resource "aws_cloudwatch_log_group" "webapp" {
  name              = "/ecs/${local.name_prefix}-webapp"
  retention_in_days = 30

  tags = { Name = "${local.name_prefix}-webapp-logs" }
}

resource "aws_ecs_task_definition" "webapp" {
  family                   = "${local.name_prefix}-webapp"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.ecs_cpu
  memory                   = var.ecs_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = "sam-webapp"
    image     = "${aws_ecr_repository.webapp.repository_url}:latest"
    essential = true

    portMappings = [{
      containerPort = var.container_port
      hostPort      = var.container_port
      protocol      = "tcp"
    }]

    environment = [
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
      { name = "PYTHONUNBUFFERED", value = "1" },
      { name = "DISABLE_AUTH", value = "0" },
      { name = "FLASK_DEBUG", value = "0" },
      { name = "AUDIT_ENABLED", value = "1" },
      { name = "AUDIT_LOG_PATH", value = "/var/log/sam/model_audit.log" },
      { name = "SAM_DB_REQUIRE_SSL", value = "false" },
      { name = "AUTH_PROVIDER", value = "oidc" },
      { name = "OIDC_REDIRECT_URI", value = "https://sam-staging.csgsam.ucar.edu/auth/oidc/callback" },
    ]

    secrets = [
      { name = "SAM_DB_SERVER", valueFrom = aws_ssm_parameter.db_host.arn },
      { name = "SAM_DB_USERNAME", valueFrom = aws_ssm_parameter.db_username.arn },
      { name = "SAM_DB_PASSWORD", valueFrom = aws_ssm_parameter.db_password.arn },
      { name = "STATUS_DB_SERVER", valueFrom = aws_ssm_parameter.db_host.arn },
      { name = "STATUS_DB_USERNAME", valueFrom = aws_ssm_parameter.db_username.arn },
      { name = "STATUS_DB_PASSWORD", valueFrom = aws_ssm_parameter.db_password.arn },
      { name = "FLASK_SECRET_KEY", valueFrom = aws_ssm_parameter.flask_secret_key.arn },
      { name = "OIDC_CLIENT_ID", valueFrom = aws_ssm_parameter.oidc_client_id.arn },
      { name = "OIDC_CLIENT_SECRET", valueFrom = aws_ssm_parameter.oidc_client_secret.arn },
      { name = "OIDC_ISSUER", valueFrom = aws_ssm_parameter.oidc_issuer.arn },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.webapp.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "ecs"
      }
    }

    linuxParameters = {
      initProcessEnabled = true
    }
  }])

  tags = { Name = "${local.name_prefix}-webapp" }
}

resource "aws_ecs_service" "webapp" {
  name                               = "${local.name_prefix}-webapp"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.webapp.arn
  desired_count                      = var.desired_count
  launch_type                        = "FARGATE"
  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 50
  enable_execute_command             = true
  health_check_grace_period_seconds  = 120

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.ecs.id]
  }

  load_balancer {
    target_group_arn = aws_alb_target_group.webapp.arn
    container_name   = "sam-webapp"
    container_port   = var.container_port
  }

  depends_on = [aws_alb_listener.http]

  lifecycle {
    ignore_changes = [task_definition]
  }

  tags = { Name = "${local.name_prefix}-webapp" }
}
