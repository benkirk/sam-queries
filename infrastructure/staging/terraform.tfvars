aws_region    = "us-east-1"
environment   = "staging"
vpc_cidr      = "10.0.0.0/16"
desired_count = 1

availability_zones = ["us-east-1a", "us-east-1b"]

ucar_ingress_cidrs = ["128.117.0.0/16"]

db_instance_class = "db.t3.micro"

container_port = 5050
ecs_cpu        = 512
ecs_memory     = 1024
