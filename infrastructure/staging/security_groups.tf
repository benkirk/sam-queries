# ALB Security Group: UCAR VPN only
resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb-sg"
  description = "ALB - allow HTTP from UCAR network only"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP from UCAR VPN"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.ucar_ingress_cidrs
  }

  ingress {
    description = "HTTPS from UCAR VPN (future)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.ucar_ingress_cidrs
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name_prefix}-alb-sg" }
}

# ECS Security Group: traffic from ALB only
resource "aws_security_group" "ecs" {
  name        = "${local.name_prefix}-ecs-sg"
  description = "ECS tasks - allow traffic from ALB"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Webapp port from ALB"
    from_port       = var.container_port
    to_port         = var.container_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name_prefix}-ecs-sg" }
}

# RDS Security Group: ECS + UCAR VPN (for direct DB access)
resource "aws_security_group" "rds" {
  name        = "${local.name_prefix}-rds-sg"
  description = "RDS - allow MySQL from ECS and UCAR VPN"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "MySQL from ECS tasks"
    from_port       = 3306
    to_port         = 3306
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  ingress {
    description = "MySQL from UCAR VPN (direct access)"
    from_port   = 3306
    to_port     = 3306
    protocol    = "tcp"
    cidr_blocks = var.ucar_ingress_cidrs
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name_prefix}-rds-sg" }
}
