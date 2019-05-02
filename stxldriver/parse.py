import collections
import html.parser


class IndexParser(html.parser.HTMLParser):
    
    def __init__(self):
        super(IndexParser, self).__init__()
        self.stack = ['ROOT']
        self.properties = collections.OrderedDict()
    
    def handle_starttag(self, tag, attrs):
        if tag in ('meta', 'link', 'img', 'li'):
            # Ignore start tags that have no matching end tag.
            # <li> should not be in this list, but this is what the camera outputs.
            return
        self.stack.append(tag)
        self.attrs = collections.defaultdict(str, attrs)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag
        
    def handle_data(self, data):
        if self.stack[-1] == 'td':
            if self.attrs['class'] == 'valuename':
                # Remember the name for a subsequent value.
                self.key = data
            elif self.attrs['class'] == 'value':
                # Save this (name, value) pair.
                self.properties[self.key] = data


class FormParser(html.parser.HTMLParser):
    
    def __init__(self):
        super(FormParser, self).__init__()
        self.forms = collections.OrderedDict()
        self.form = None
        self.form_name = None
    
    def handle_starttag(self, tag, attrs):
        attrs = collections.defaultdict(str, attrs)
        if tag == 'form':
            # Start parsing a new form.
            name = attrs['name']
            if name in self.forms:
                raise RuntimeError(f'Found duplicate form with name "{name}".')
            self.form = self.forms[name] = collections.OrderedDict()
            self.form_name = name
            return
        if tag != 'input':
            return
        # Update the current form.
        if self.form is None:
            raise RuntimeError(f'Found orphan form input with attrs: {attrs}.')
        itype = attrs['type']
        if itype not in ('radio', 'text', 'hidden', 'submit', 'button'):
            raise RuntimeError(
                f'Found bad input type in "{self.form_name}" with attrs {attrs}.')
        name, value = attrs['name'], attrs['value']
        if itype in ('submit', 'button'):
            pass
        elif itype in ('text', 'hidden'):
            if name in self.form:
                raise RuntimeError(
                    f'Found duplicate input "{name}" in "{self.form_name}".')
            self.form[name] = value
        elif itype == 'radio':            
            # Record all allowed values.
            values_key = f'_{name}_values'
            if values_key not in self.form:
                self.form[values_key] = [value]
            else:
                self.form[values_key].append(value)
            # Record the one checked value.
            if 'checked' in attrs:
                if name in self.form:
                    raise RuntimeError(
                        f'Found duplicate checked value for "{input}" in "{self.form_name}".')
                self.form[name] = value
        
    def handle_endtag(self, tag):
        if tag == 'form':
            self.form = None
            self.form_name = None
