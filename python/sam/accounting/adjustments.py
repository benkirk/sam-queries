#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class ChargeAdjustment(Base):
    """Manual adjustments to account charges."""
    __tablename__ = 'charge_adjustment'

    __table_args__ = (
        Index('ix_charge_adjustment_account', 'account_id'),
        Index('ix_charge_adjustment_type', 'charge_adjustment_type_id'),
        Index('ix_charge_adjustment_adjusted_by', 'adjusted_by_id'),
    )

    charge_adjustment_id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)
    charge_adjustment_type_id = Column(Integer,
                                       ForeignKey('charge_adjustment_type.charge_adjustment_type_id'),
                                       nullable=False)
    amount = Column(Float, nullable=False)
    comment = Column(Text)
    adjustment_date = Column(DateTime, nullable=False)
    adjusted_by_id = Column(Integer, ForeignKey('users.user_id'))

    account = relationship('Account', back_populates='charge_adjustments')
    adjustment_type = relationship('ChargeAdjustmentType', back_populates='adjustments')
    adjusted_by = relationship('User', back_populates='charge_adjustments_made')


# ============================================================================
# Access Control
# ============================================================================


#----------------------------------------------------------------------------
class ChargeAdjustmentType(Base, TimestampMixin):
    """Types of charge adjustments (Credit, Debit, Refund)."""
    __tablename__ = 'charge_adjustment_type'

    charge_adjustment_type_id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(20), nullable=False)

    adjustments = relationship('ChargeAdjustment', back_populates='adjustment_type')

    def __str__(self):
        return f"{self.type}"

    def __repr__(self):
        return f"<ChargeAdjustmentType(type='{self.type}')>"


#-------------------------------------------------------------------------em-
