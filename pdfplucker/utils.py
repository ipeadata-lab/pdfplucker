# utils.py

from concurrent.futures import ProcessPoolExecutor
import multiprocessing
from docling.datamodel.document import ConversionResult
from docling_core.types.doc import (
    PictureItem,
    TableItem,
    TextItem,
    DocItemLabel,
)
from typing import TypedDict, List, Dict, Any
import logging
import os
import traceback

class Data(TypedDict):
    metadata: Dict[str, Any]
    pages: List[Dict[str, Any]]
    images: List[Dict[str, Any]]
    tables: List[Dict[str, Any]]
    captions: List[Dict[str, Any]]

def format_results(conv: ConversionResult, data: Data, filename: str, image_path: str) -> bool:
    ''' Uses the docling document to format a readable JSON result '''
    
    try:
        # Pré-inicializa o dicionário de páginas para evitar verificações repetidas
        pages_dict = {}
        for page_dict in data['pages']:
            pages_dict[page_dict['page_number']] = page_dict
            if 'content' not in page_dict:
                page_dict['content'] = ""
        
        # Processa captions antecipadamente para uso posterior
        caption_dict = {}
        
        counter = 0
        for idx, (item, _) in enumerate(conv.document.iterate_items()):
            if isinstance(item, TextItem):
                page = item.prov[0].page_no
                label = item.label
                text = item.text
                
                # Criar ou obter a página uma única vez
                if page not in pages_dict:
                    new_page = {'page_number': page, 'content': ""}
                    data['pages'].append(new_page)
                    pages_dict[page] = new_page
                
                # Determina o conteúdo baseado no label
                match label:
                    case DocItemLabel.SECTION_HEADER:
                        content = f"\n# {text}\n"
                    case DocItemLabel.FORMULA:
                        content = f" Equation: {text}\n"
                    case DocItemLabel.REFERENCE:
                        content = f"\nReference: {text}\n"
                    case DocItemLabel.LIST_ITEM:
                        content = f"\n- {text}\n"
                    case DocItemLabel.CAPTION:
                        content = f" _{text}_\n"
                        # Armazena caption para uso posterior
                        data['captions'].append({
                            'self_ref': item.self_ref,
                            'cref': item.parent.cref,
                            'text': text
                        })
                        # Também pré-processa para uso posterior
                        caption_dict[item.parent.cref] = text
                    case DocItemLabel.FOOTNOTE:
                        content = f"\nFootnote: {text}\n"
                    case DocItemLabel.TITLE:
                        content = f"\n## {text}\n"
                    case DocItemLabel.TEXT:
                        content = f" {text}"
                    case DocItemLabel.PARAGRAPH:
                        content = f"\n{text}\n"
                    case DocItemLabel.PAGE_FOOTER:
                        content = f"\n{text}\n"
                    case DocItemLabel.CHECKBOX_SELECTED:
                        content = f"\n- {text}\n"
                    case DocItemLabel.CHECKBOX_UNSELECTED:
                        content = f"\n- {text}\n"
                    case _:
                        content = f" {text}"
                
                # Adiciona o conteúdo à página
                pages_dict[page]['content'] += content
                    
            elif isinstance(item, TableItem):
                table = item.export_to_markdown(doc=conv.document)
                self_ref = item.self_ref
                page = item.prov[0].page_no
                
                # Criar ou obter a página uma única vez
                if page not in pages_dict:
                    new_page = {'page_number': page, 'content': f" <{self_ref}>"}
                    data['pages'].append(new_page)
                    pages_dict[page] = new_page
                else:
                    pages_dict[page]['content'] += f" <{self_ref}>"
                
                data['tables'].append({
                    'self_ref': self_ref,
                    'captions': item.captions,
                    'caption': "",
                    'references': item.references,
                    'footnotes': item.footnotes,
                    'page': page,
                    'table': table
                })
    
            elif isinstance(item, PictureItem):
                self_ref = item.self_ref
                page = item.prov[0].page_no
                
                # Extrair classificação, se disponível
                classification = None
                confidence = None
                if item.annotations:
                    for annotation in item.annotations:
                        if annotation.kind == 'classification':
                            best_class = max(
                                annotation.predicted_classes,
                                key=lambda cls: cls.confidence
                            )
                            classification = best_class.class_name
                            confidence = best_class.confidence
                            break
                
                # Salva a imagem
                image_filename = (image_path / f"{filename}_{counter}.png")
                placeholder = f"{filename}_{counter}.png"
                with image_filename.open('wb') as file:
                    item.get_image(conv.document).save(file, "PNG")
                
                # Criar ou obter a página uma única vez
                if page not in pages_dict:
                    new_page = {'page_number': page, 'content': f" <{placeholder}>"}
                    data['pages'].append(new_page)
                    pages_dict[page] = new_page
                else:
                    pages_dict[page]['content'] += f" <{placeholder}>"
                
                data['images'].append({
                    'ref': placeholder,
                    'self_ref': self_ref,
                    'captions': item.captions,
                    'caption': "",
                    'classification': classification,
                    'confidence': confidence,
                    'references1': item.references,
                    'references': [],
                    'footnotes1': item.footnotes,
                    'footnotes': [],
                    'page': page,
                })
                counter += 1

        # Processa as referências de texto e aplica captions
        text_refs = {}
        for text in conv.document.texts:
            if text.label == DocItemLabel.TEXT:
                text_refs[text.self_ref] = text.text
        
        # Aplica captions, referências e notas de rodapé em uma única iteração
        for image in data.get("images", []):
            # Aplica caption
            self_ref = image.get("self_ref")
            if self_ref in caption_dict:
                image["caption"] += caption_dict[self_ref]
            
            # Aplica referências
            for ref in image.get("references1", []):
                ref_key = getattr(ref, 'self_ref', str(ref))
                if ref_key in text_refs:
                    image['references'].append(text_refs[ref])
            
            # Aplica notas de rodapé
            for footnote in image.get("footnotes1", []):
                footnote_key = getattr(footnote, 'self_ref', str(footnote))
                if footnote_key in text_refs:
                    image['footnotes'].append(text_refs[footnote])
            
            # Remove campos temporários
            image.pop('captions')
            image.pop('references1')
            image.pop('footnotes1')

        # Mesmo processo para tabelas
        for table in data.get("tables", []):
            # Aplica caption
            self_ref = table.get("self_ref")
            if self_ref in caption_dict:
                table["caption"] += caption_dict[self_ref]
            
            # Aplica referências
            for ref in table.get("references", []):
                ref_key = getattr(ref, 'self_ref', str(ref))
                if ref_key in text_refs:
                    table['references'].append(text_refs[ref])
            
            # Aplica notas de rodapé
            for footnote in table.get("footnotes", []):
                footnote_key = getattr(footnote, 'self_ref', str(footnote))
                if footnote_key in text_refs:
                    table['footnotes'].append(text_refs[footnote])
            
            # Remove campos temporários
            table.pop('captions')
            if 'references1' in table:
                table.pop('references1')
            if 'footnotes1' in table:
                table.pop('footnotes1')

        # Remove captions temporárias
        data.pop('captions')
        
        return True
    except Exception as e:
        print(f"\033[33mError formatting result: {e}\033[0m")
        traceback.print_exc()
        return False

def get_safe_executor(max_workers=None):
    try:
        multiprocessing.set_start_method('spawn', force=True)
    except RuntimeError:
        # Method might already be set
        pass
        
    return ProcessPoolExecutor(max_workers=max_workers)

class ColorfulFormatter(logging.Formatter):
    COLOURS = {
        'DEBUG': '\033[94m',  # Blue
        'INFO': '\033[92m',   # Green
        'WARNING': '\033[93m',  # Yellow
        'ERROR': '\033[91m',  # Red
        'CRITICAL': '\033[95m',  # Magenta
    }
    RESET = '\033[0m'  # Reset to default color

    def format(self, record):
        log_color = self.COLOURS.get(record.levelname, self.RESET)
        record.levelname = f"{log_color}{record.levelname}{self.RESET}"
   
        return super().format(record)

def setup_logging(level=logging.INFO):
    # Create formatters
    detailed_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    simple_formatter = ColorfulFormatter('%(asctime)s - %(levelname)s - %(message)s')
    
    # Create file handlers
    log_dir = os.path.join(os.getcwd(), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    
    pdfplucker_file = logging.FileHandler(os.path.join(log_dir, 'pdfplucker.log'))
    pdfplucker_file.setFormatter(detailed_formatter)
    
    # Create console handler
    console = logging.StreamHandler()
    console.setFormatter(simple_formatter)
    
    # Create a third-party log file for docling
    third_party_file = logging.FileHandler(os.path.join(log_dir, 'dependencies.log'))
    third_party_file.setFormatter(detailed_formatter)
    third_party_file.setLevel(logging.DEBUG)  # All levels
    
    # Configure the root logger (for third-party libraries)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # All levels
    
    # Remove any existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    root_logger.addHandler(third_party_file)
    
    # Configure your package's logger
    pdfplucker_logger = logging.getLogger('pdfplucker')
    pdfplucker_logger.setLevel(level)
    
    # Remove any existing handlers to avoid duplicates
    for handler in pdfplucker_logger.handlers[:]:
        pdfplucker_logger.removeHandler(handler)
    
    pdfplucker_logger.addHandler(pdfplucker_file)
    pdfplucker_logger.addHandler(console)
    
    # Make sure your logger doesn't propagate to root
    pdfplucker_logger.propagate = False
    
    return pdfplucker_logger

logger = setup_logging()
