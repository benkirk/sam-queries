#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class ArchiveActivity(Base, TimestampMixin):
    """Archive (HPSS) activity records."""
    __tablename__ = 'archive_activity'

    __table_args__ = (
        Index('ix_archive_activity_type', 'type_act'),
        Index('ix_archive_activity_cos', 'archive_cos_id'),
    )

    archive_activity_id = Column(Integer, primary_key=True, autoincrement=True)
    archive_resource = Column(String(5), nullable=False)
    type_act = Column(String(1), nullable=False)
    reporting_interval = Column(Integer)
    activity_date = Column(DateTime, nullable=False)

    number_of_files = Column(Integer, nullable=False)
    bytes = Column(BigInteger, nullable=False)

    dns = Column(String(100))
    unix_uid = Column(Integer, nullable=False)
    username = Column(String(30))
    projcode = Column(String(30), nullable=False)

    load_date = Column(DateTime, nullable=False)
    processing_status = Column(Boolean)
    error_comment = Column(Text)

    archive_cos_id = Column(Integer, ForeignKey('archive_cos.archive_cos_id'))

    archive_cos = relationship('ArchiveCos', back_populates='activities')
    charges = relationship('ArchiveCharge', back_populates='activity')

    def __eq__(self, other):
        """Two activities are equal if they have the same archive_activity_id."""
        if not isinstance(other, ArchiveActivity):
            return False
        return (self.archive_activity_id is not None and
                self.archive_activity_id == other.archive_activity_id)

    def __hash__(self):
        """Hash based on archive_activity_id for set/dict operations."""
        return (hash(self.archive_activity_id) if self.archive_activity_id is not None
                else hash(id(self)))


#----------------------------------------------------------------------------
class ArchiveCharge(Base):
    """Archive charges derived from activity."""
    __tablename__ = 'archive_charge'

    __table_args__ = (
        Index('ix_archive_charge_account', 'account_id'),
        Index('ix_archive_charge_user', 'user_id'),
        Index('ix_archive_charge_activity', 'archive_activity_id', unique=True),
        Index('ix_archive_charge_date', 'charge_date'),
    )

    archive_charge_id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)
    charge_date = Column(DateTime, nullable=False)
    archive_activity_id = Column(Integer, ForeignKey('archive_activity.archive_activity_id'),
                                 nullable=False, unique=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    charge = Column(Numeric(22, 8))
    terabyte_year = Column(Numeric(22, 8))
    activity_date = Column(DateTime)

    account = relationship('Account', back_populates='archive_charges')
    activity = relationship('ArchiveActivity', back_populates='charges')
    user = relationship('User', back_populates='archive_charges')


#----------------------------------------------------------------------------
class ArchiveCos(Base, TimestampMixin):
    """Archive Class of Service definitions."""
    __tablename__ = 'archive_cos'

    archive_cos_id = Column(Integer, primary_key=True)
    number_of_copies = Column(Integer, nullable=False)
    description = Column(String(255), nullable=False)

    activities = relationship('ArchiveActivity', back_populates='archive_cos')


#-------------------------------------------------------------------------em-
