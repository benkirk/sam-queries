#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class DatasetActivity(Base, TimestampMixin):
    """Dataset usage tracking for project directories."""
    __tablename__ = 'dataset_activity'

    __table_args__ = (
        Index('ix_dataset_activity_date', 'activity_date'),
        Index('ix_dataset_activity_directory', 'project_directory'),
    )

    activity_id = Column(Integer, primary_key=True, autoincrement=True)
    activity_date = Column(DateTime, nullable=False)

    # Directory and dataset information
    project_directory = Column(String(255), nullable=False)
    dataset = Column(String(255), nullable=False)

    # Usage metrics
    reporting_interval = Column(Integer, nullable=False)
    bytes = Column(BigInteger, nullable=False)
    number_of_files = Column(Integer, nullable=False)

    def __str__(self):
        return f"{self.project_directory} - {self.dataset}"

    def __repr__(self):
        return (f"<DatasetActivity(directory='{self.project_directory}', "
                f"dataset='{self.dataset}', bytes={self.bytes})>")


# ============================================================================
# Disk Resource Root Directory
# ============================================================================


#-------------------------------------------------------------------------em-
