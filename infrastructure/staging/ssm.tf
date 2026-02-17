resource "aws_ssm_parameter" "db_host" {
  name  = "/sam/${var.environment}/db-host"
  type  = "String"
  value = aws_db_instance.main.address

  tags = { Name = "${local.name_prefix}-db-host" }
}

resource "aws_ssm_parameter" "db_username" {
  name  = "/sam/${var.environment}/db-username"
  type  = "SecureString"
  value = var.db_username

  tags = { Name = "${local.name_prefix}-db-username" }
}

resource "aws_ssm_parameter" "db_password" {
  name  = "/sam/${var.environment}/db-password"
  type  = "SecureString"
  value = var.db_password

  tags = { Name = "${local.name_prefix}-db-password" }
}

resource "aws_ssm_parameter" "flask_secret_key" {
  name  = "/sam/${var.environment}/flask-secret-key"
  type  = "SecureString"
  value = var.flask_secret_key

  tags = { Name = "${local.name_prefix}-flask-secret-key" }
}
