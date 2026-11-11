"""The webapp's markup-emitting Jinja filters."""
from markupsafe import Markup

from webapp.utils.template_filters import dom_id, path_breaks


class TestPathBreaks:
    def test_a_wbr_follows_every_slash(self):
        out = path_breaks('/gpfs/csfs1/univ/ucir0064')
        assert out == '/<wbr>gpfs/<wbr>csfs1/<wbr>univ/<wbr>ucir0064'
        assert isinstance(out, Markup)

    def test_markup_in_the_path_is_escaped_before_the_breaks_go_in(self):
        out = path_breaks('/glade/<script>alert(1)</script>')
        assert '<script>' not in out
        assert out == '/<wbr>glade/<wbr>&lt;script&gt;alert(1)&lt;/<wbr>script&gt;'

    def test_none_and_a_slashless_name_pass_through(self):
        assert path_breaks(None) == ''
        assert path_breaks('scratch') == 'scratch'

    def test_registered_on_the_app_and_not_double_escaped(self, app):
        tpl = app.jinja_env.from_string('<code>{{ p | path_breaks }}</code>')
        assert tpl.render(p='/gpfs/csfs1/a&b') == '<code>/<wbr>gpfs/<wbr>csfs1/<wbr>a&amp;b</code>'


class TestDomId:
    def test_selector_characters_become_underscores(self):
        assert dom_id('j.doe') == 'j_doe'
        assert dom_id('a b#c:d') == 'a_b_c_d'

    def test_an_id_safe_value_is_unchanged(self):
        assert dom_id('UCUB0208') == 'UCUB0208'
        assert dom_id('ben_kirk-2') == 'ben_kirk-2'

    def test_registered_on_the_app(self, app):
        assert app.jinja_env.from_string("{{ 'x.y' | dom_id }}").render() == 'x_y'
