output "alb_dns_name" {
  description = "ALB DNS name (use this to access staging)"
  value       = aws_alb.main.dns_name
}

output "ecr_repository_url" {
  description = "ECR repository URL for Docker images"
  value       = aws_ecr_repository.webapp.repository_url
}

output "rds_endpoint" {
  description = "RDS MySQL endpoint"
  value       = aws_db_instance.main.endpoint
}

output "rds_address" {
  description = "RDS MySQL hostname (without port)"
  value       = aws_db_instance.main.address
}

output "ecs_cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "ECS service name"
  value       = aws_ecs_service.webapp.name
}

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "staging_url" {
  description = "Staging site URL"
  value       = "http://${aws_alb.main.dns_name}"
}
