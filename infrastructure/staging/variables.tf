variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "staging"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "Availability zones"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]
}

variable "ucar_ingress_cidrs" {
  description = "UCAR VPN CIDR blocks allowed to access the staging environment"
  type        = list(string)
  default     = ["128.117.0.0/16"]
}

variable "db_username" {
  description = "RDS master username"
  type        = string
  default     = "samadmin"
  sensitive   = true
}

variable "db_password" {
  description = "RDS master password"
  type        = string
  sensitive   = true
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.micro"
}

variable "container_port" {
  description = "Port the webapp container listens on"
  type        = number
  default     = 5050
}

variable "ecs_cpu" {
  description = "ECS task CPU units"
  type        = number
  default     = 1024
}

variable "ecs_memory" {
  description = "ECS task memory (MiB)"
  type        = number
  default     = 2048
}

variable "desired_count" {
  description = "Number of ECS tasks to run"
  type        = number
  default     = 1
}

variable "flask_secret_key" {
  description = "Flask SECRET_KEY for session signing"
  type        = string
  sensitive   = true
}
