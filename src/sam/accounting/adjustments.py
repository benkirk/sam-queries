#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


# Sign policy for user-visible adjustment types. Keys MUST match
# ChargeAdjustmentType.type values exactly. Credits/Refunds return compute
# to a project (stored as negative amounts); Debits/Reservations remove it
# (positive amounts). This map is the single source of truth for which
# types the webapp exposes — the integer PKs are resolved at runtime via
# ChargeAdjustment.supported_types(). Storage-Credit and Storage-Debit are
# intentionally omitted until disk/archive support lands.
_SIGN_BY_TYPE: Dict[str, int] = {
    'Refund':      -1,
    'Credit':      -1,
    'Debit':       +1,
    'Reservation': +1,
}


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

    @classmethod
    def supported_types(cls, session) -> List['ChargeAdjustmentType']:
        """Return ChargeAdjustmentType rows exposed by the webapp.

        Ordered to match _SIGN_BY_TYPE insertion order (Refund first, since
        it's the most-used type in practice — see legacy docs §5).
        """
        rows = (session.query(ChargeAdjustmentType)
                       .filter(ChargeAdjustmentType.type.in_(_SIGN_BY_TYPE.keys()))
                       .all())
        order = list(_SIGN_BY_TYPE.keys())
        return sorted(rows, key=lambda t: order.index(t.type))

    @classmethod
    def create(cls, session, *,
               account_id: int,
               charge_adjustment_type_id: int,
               amount: float,
               adjusted_by_id: int,
               comment: Optional[str] = None) -> 'ChargeAdjustment':
        """Create a ChargeAdjustment; server applies sign from the type.

        The caller passes ``amount`` as a positive number. The sign is
        applied here based on the type's name (Credits/Refunds → negative,
        Debits/Reservations → positive). This guarantees the stored amount
        is correct regardless of UI input, matching the legacy Sign
        Enforcement pattern (see legacy_sam/doc/data_structures/
        charge_adjustments.md §2).

        Does NOT commit — caller must wrap in ``management_transaction``.
        """
        if amount is None or amount <= 0:
            raise ValueError("amount must be a positive number")

        adj_type = session.get(ChargeAdjustmentType, charge_adjustment_type_id)
        if adj_type is None:
            raise ValueError(
                f"ChargeAdjustmentType {charge_adjustment_type_id} not found"
            )
        try:
            sign = _SIGN_BY_TYPE[adj_type.type]
        except KeyError:
            raise ValueError(
                f"Adjustment type '{adj_type.type}' is not supported in the webapp"
            )

        adj = cls(
            account_id=account_id,
            charge_adjustment_type_id=charge_adjustment_type_id,
            amount=sign * float(amount),
            comment=(comment or None),
            adjustment_date=datetime.now(),
            adjusted_by_id=adjusted_by_id,
        )
        session.add(adj)
        session.flush()
        return adj

    def __str__(self):
        return f"ChargeAdjustment {self.charge_adjustment_id}: {self.amount} ({self.adjustment_date})"

    def __repr__(self):
        return f"<ChargeAdjustment(id={self.charge_adjustment_id}, account_id={self.account_id}, amount={self.amount})>"


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
