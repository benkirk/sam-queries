#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class Factor(Base, TimestampMixin):
    """
    Charging factors for resource types.

    Factors are named variables used in charging formulas.
    They can have time-based validity periods (start_date/end_date).

    Example factors:
    - WCH (wall_clock_hours)
    - HPSS reads
    - GLADE stored data factor
    """
    __tablename__ = 'factor'

    __table_args__ = (
        Index('factor_resource_type_fk', 'resource_type_id'),
    )

    factor_id = Column(Integer, primary_key=True, autoincrement=True)
    resource_type_id = Column(Integer, ForeignKey('resource_type.resource_type_id'), nullable=False)
    factor_name = Column(String(50), nullable=False)
    value = Column(String(255), nullable=False)

    # Validity period
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)

    # Relationships
    resource_type = relationship('ResourceType', back_populates='factors')

    @property
    def is_active(self) -> bool:
        """Check if factor is currently active based on date range."""
        from datetime import datetime
        now = datetime.now()  # Use naive datetime to match database
        if self.end_date:
            return self.start_date <= now <= self.end_date
        return self.start_date <= now

    def __str__(self):
        return f"{self.factor_name}: {self.value}"

    def __repr__(self):
        return f"<Factor(id={self.factor_id}, name='{self.factor_name}', value='{self.value}')>"


#----------------------------------------------------------------------------
class Formula(Base, TimestampMixin):
    """
    Charging formulas for resource types.

    Formulas define how charges are calculated using factors and activity data.
    They use a template syntax with @{variable_name} placeholders.

    Example formulas:
    - Exclusive jobs: @{wall_clock_hours}*@{number_of_nodes}*@{queue_factor}
    - Storage: @{gigabyte_years}/1000
    - Share jobs: @{cpu_hours}*@{queue_factor}
    """
    __tablename__ = 'formula'

    __table_args__ = (
        Index('formula_resource_type_fk', 'resource_type_id'),
    )

    formula_id = Column(Integer, primary_key=True, autoincrement=True)
    resource_type_id = Column(Integer, ForeignKey('resource_type.resource_type_id'), nullable=False)
    formula_name = Column(String(50), nullable=False)
    formula_str = Column(String(1024), nullable=False)

    # Validity period
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)

    # Relationships
    resource_type = relationship('ResourceType', back_populates='formulas')

    @property
    def is_active(self) -> bool:
        """Check if formula is currently active based on date range."""
        from datetime import datetime
        now = datetime.now()  # Use naive datetime to match database
        if self.end_date:
            return self.start_date <= now <= self.end_date
        return self.start_date <= now

    @property
    def variables(self) -> list:
        """Extract variable names from formula string."""
        import re
        return re.findall(r'@\{([^}]+)\}', self.formula_str)

    def __str__(self):
        return f"{self.formula_name}"

    def __repr__(self):
        return f"<Formula(id={self.formula_id}, name='{self.formula_name}')>"


#-------------------------------------------------------------------------em-
