#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class DiskActivity(Base, TimestampMixin):
    """Disk usage activity records."""
    __tablename__ = 'disk_activity'

    __table_args__ = (
        Index('ix_disk_activity_directory', 'directory_name'),
        Index('ix_disk_activity_cos', 'disk_cos_id'),
    )

    disk_activity_id = Column(Integer, primary_key=True, autoincrement=True)
    directory_name = Column(String(255), nullable=False)
    username = Column(String(35), nullable=False)
    projcode = Column(String(30))
    activity_date = Column(DateTime, nullable=False)
    reporting_interval = Column(Integer, nullable=False)

    file_size_total = Column(BigInteger, nullable=False)
    bytes = Column(BigInteger, nullable=False)
    number_of_files = Column(Integer)

    load_date = Column(DateTime, nullable=False)
    disk_cos_id = Column(Integer, ForeignKey('disk_cos.disk_cos_id'), nullable=False)

    error_comment = Column(Text)
    processing_status = Column(Boolean)
    resource_name = Column(String(40))

    disk_cos = relationship('DiskCos', back_populates='activities')
    charges = relationship('DiskCharge', back_populates='activity')

    def __eq__(self, other):
        """Two activities are equal if they have the same disk_activity_id."""
        if not isinstance(other, DiskActivity):
            return False
        return (self.disk_activity_id is not None and
                self.disk_activity_id == other.disk_activity_id)

    def __hash__(self):
        """Hash based on disk_activity_id for set/dict operations."""
        return (hash(self.disk_activity_id) if self.disk_activity_id is not None
                else hash(id(self)))


#----------------------------------------------------------------------------
class DiskCharge(Base):
    """Disk charges derived from activity."""
    __tablename__ = 'disk_charge'

    __table_args__ = (
        Index('ix_disk_charge_account', 'account_id'),
        Index('ix_disk_charge_user', 'user_id'),
        Index('ix_disk_charge_activity', 'disk_activity_id', unique=True),
        Index('ix_disk_charge_date', 'charge_date'),
    )

    disk_charge_id = Column(Integer, primary_key=True, autoincrement=True)
    disk_activity_id = Column(Integer, ForeignKey('disk_activity.disk_activity_id'),
                              nullable=False, unique=True)
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)
    charge_date = Column(DateTime, nullable=False)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    charge = Column(Numeric(22, 8))
    terabyte_year = Column(Numeric(22, 8))
    activity_date = Column(DateTime)

    account = relationship('Account', back_populates='disk_charges')
    activity = relationship('DiskActivity', back_populates='charges')
    user = relationship('User', back_populates='disk_charges')


#----------------------------------------------------------------------------
class DiskCos(Base, TimestampMixin):
    """Disk Class of Service definitions."""
    __tablename__ = 'disk_cos'

    disk_cos_id = Column(Integer, primary_key=True)
    description = Column(String(255), nullable=False)

    activities = relationship('DiskActivity', back_populates='disk_cos')

    def __str__(self):
        return f"{self.description}"

#-------------------------------------------------------------------------em-
