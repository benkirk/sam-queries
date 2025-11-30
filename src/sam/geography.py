#-------------------------------------------------------------------------bh-
# Common Imports:
from .base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class Country(Base, TimestampMixin, SoftDeleteMixin):
    """Countries for address information."""
    __tablename__ = 'country'

    ext_country_id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(2), nullable=False)
    name = Column(String(50), nullable=False)

    state_provs = relationship('StateProv', back_populates='country')

    def __str__(self):
        return f"{self.code} - {self.name}"

    def __repr__(self):
        return f"<Country(code='{self.code}', name='{self.name}')>"


#----------------------------------------------------------------------------
class StateProv(Base, TimestampMixin, SoftDeleteMixin):
    """U.S. states and international provinces."""
    __tablename__ = 'state_prov'

    ext_state_prov_id = Column(Integer, primary_key=True, autoincrement=True)
    ext_country_id = Column(Integer, ForeignKey('country.ext_country_id'), nullable=False)
    name = Column(String(100), nullable=False)
    code = Column(String(15))

    country = relationship('Country', back_populates='state_provs')
    institutions = relationship('Institution', back_populates='state_prov')

    def __str__(self):
        return f"{self.code} - {self.name}"

    def __repr__(self):
        return f"<StateProv(code='{self.code}', name='{self.name}')>"


# ============================================================================
# User Management
# ============================================================================


#-------------------------------------------------------------------------em-
