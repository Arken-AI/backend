"""
PDF Report Generator Service

Responsible for generating professional PDF reports from simulation data
using ReportLab library. Creates multi-page reports with:
- Cover page
- Table of contents
- Process descriptions (placeholder for AI content in Phase 2)
- PFD image
- Stream tables
- Equipment tables
- Mass/energy balance summaries
"""

import io
import base64
from typing import List, Optional, Tuple
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    Image,
    KeepTogether,
)
from reportlab.platypus.flowables import HRFlowable
from PIL import Image as PILImage

from app.models.report import (
    ReportData,
    ReportMetadata,
    StreamTableData,
    EquipmentTableData,
    MassBalanceSummary,
    EnergyBalanceSummary,
    ReportSection,
)
from app.services.table_formatter import (
    StreamTableFormatter,
    format_number,
    format_with_thousands_separator,
    create_balance_summary_table,
)


# =============================================================================
# PAGE SIZE MAPPING
# =============================================================================

PAGE_SIZES = {
    "letter": letter,
    "a4": A4,
}


class PDFReportGenerator:
    """
    Generates professional PDF reports from simulation data.
    
    Features:
    - Cover page with metadata
    - Table of contents
    - Embedded PFD images
    - Formatted stream and equipment tables
    - Mass and energy balance summaries
    - Placeholder sections for AI-generated content (Phase 2)
    """
    
    def __init__(self, page_size: str = "letter", max_streams_per_table: int = 10):
        """
        Initialize the PDF generator.
        
        Args:
            page_size: Page size ("letter" or "a4")
            max_streams_per_table: Maximum streams per table before splitting
        """
        self.page_size = PAGE_SIZES.get(page_size.lower(), letter)
        self.page_width, self.page_height = self.page_size
        self.max_streams_per_table = max_streams_per_table
        self.table_formatter = StreamTableFormatter(max_streams_per_table)
        self.styles = self._setup_styles()
        
        # Track page numbers for TOC (simplified - actual implementation would use canvas)
        self.section_pages = {}
    
    def _setup_styles(self) -> dict:
        """
        Set up paragraph and table styles for the report.
        
        Returns:
            Dictionary of named styles
        """
        base_styles = getSampleStyleSheet()
        styles = {}
        
        # Title style (cover page)
        styles['title'] = ParagraphStyle(
            'ReportTitle',
            parent=base_styles['Title'],
            fontSize=28,
            spaceAfter=30,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#1a365d'),
            fontName='Helvetica-Bold',
        )
        
        # Subtitle style
        styles['subtitle'] = ParagraphStyle(
            'Subtitle',
            parent=base_styles['Normal'],
            fontSize=16,
            spaceAfter=20,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#4a5568'),
        )
        
        # Heading 1 style (section headers)
        styles['heading1'] = ParagraphStyle(
            'Heading1',
            parent=base_styles['Heading1'],
            fontSize=18,
            spaceBefore=20,
            spaceAfter=12,
            textColor=colors.HexColor('#1a365d'),
            fontName='Helvetica-Bold',
        )
        
        # Heading 2 style (subsection headers)
        styles['heading2'] = ParagraphStyle(
            'Heading2',
            parent=base_styles['Heading2'],
            fontSize=14,
            spaceBefore=15,
            spaceAfter=8,
            textColor=colors.HexColor('#2d3748'),
            fontName='Helvetica-Bold',
        )
        
        # Body text style
        styles['body'] = ParagraphStyle(
            'BodyText',
            parent=base_styles['Normal'],
            fontSize=11,
            spaceBefore=6,
            spaceAfter=6,
            alignment=TA_JUSTIFY,
            leading=14,
        )
        
        # Caption style (for figures and tables)
        styles['caption'] = ParagraphStyle(
            'Caption',
            parent=base_styles['Normal'],
            fontSize=10,
            spaceBefore=6,
            spaceAfter=12,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#4a5568'),
            fontName='Helvetica-Oblique',
        )
        
        # TOC entry style
        styles['toc_entry'] = ParagraphStyle(
            'TOCEntry',
            parent=base_styles['Normal'],
            fontSize=12,
            spaceBefore=4,
            spaceAfter=4,
            leftIndent=20,
        )
        
        # Metadata style (cover page info)
        styles['metadata'] = ParagraphStyle(
            'Metadata',
            parent=base_styles['Normal'],
            fontSize=12,
            spaceBefore=8,
            spaceAfter=8,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#4a5568'),
        )
        
        return styles
    
    def _get_table_style(self, has_header: bool = True) -> TableStyle:
        """
        Get standard table style for data tables.
        
        Args:
            has_header: Whether the table has a header row
            
        Returns:
            TableStyle object
        """
        style_commands = [
            # Grid
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e0')),
            # Alignment
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),  # First column left-aligned
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            # Padding
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            # Font
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
        ]
        
        if has_header:
            style_commands.extend([
                # Header row styling
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0')),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
            ])
        
        return TableStyle(style_commands)
    
    def add_cover_page(self, story: List, metadata: ReportMetadata):
        """
        Add cover page to the report.
        
        Args:
            story: List of flowables to append to
            metadata: Report metadata
        """
        # Add vertical space to center content
        story.append(Spacer(1, 2 * inch))
        
        # Report title
        story.append(Paragraph(metadata.title, self.styles['title']))
        
        # Process name
        story.append(Paragraph(
            f"Process: {metadata.process_name}",
            self.styles['subtitle']
        ))
        
        story.append(Spacer(1, 1 * inch))
        
        # Horizontal rule
        story.append(HRFlowable(
            width="60%",
            thickness=2,
            color=colors.HexColor('#1a365d'),
            spaceBefore=20,
            spaceAfter=20,
            hAlign='CENTER'
        ))
        
        story.append(Spacer(1, 0.5 * inch))
        
        # Metadata section
        story.append(Paragraph(
            f"Run ID: {metadata.run_id}",
            self.styles['metadata']
        ))
        
        story.append(Paragraph(
            f"Generated: {metadata.date_generated.strftime('%B %d, %Y at %H:%M UTC')}",
            self.styles['metadata']
        ))
        
        story.append(Paragraph(
            f"Generated by: {metadata.author}",
            self.styles['metadata']
        ))
        
        story.append(Paragraph(
            f"Version: {metadata.version}",
            self.styles['metadata']
        ))
        
        # Page break after cover
        story.append(PageBreak())
    
    def add_table_of_contents(self, story: List):
        """
        Add table of contents to the report.
        
        Args:
            story: List of flowables to append to
        """
        story.append(Paragraph("Table of Contents", self.styles['heading1']))
        story.append(Spacer(1, 0.3 * inch))
        
        # Static TOC entries (page numbers are approximate)
        toc_entries = [
            ("1. Executive Summary", "3"),
            ("2. Process Description", "4"),
            ("3. Process Flow Diagram", "5"),
            ("4. Stream Tables", "6"),
            ("5. Equipment Summary", "8"),
            ("6. Mass & Energy Balance", "9"),
            ("7. Observations & Conclusions", "10"),
        ]
        
        for entry, page in toc_entries:
            # Create a two-column table for TOC entry
            toc_row = [[entry, page]]
            toc_table = Table(toc_row, colWidths=[5 * inch, 0.5 * inch])
            toc_table.setStyle(TableStyle([
                ('ALIGN', (0, 0), (0, 0), 'LEFT'),
                ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 12),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ]))
            story.append(toc_table)
        
        story.append(PageBreak())
    
    def add_section_header(self, story: List, section_number: str, title: str):
        """
        Add a section header to the report.
        
        Args:
            story: List of flowables to append to
            section_number: Section number (e.g., "1", "2.1")
            title: Section title
        """
        story.append(Paragraph(
            f"{section_number}. {title}",
            self.styles['heading1']
        ))
    
    def add_subsection_header(self, story: List, section_number: str, title: str):
        """
        Add a subsection header to the report.
        
        Args:
            story: List of flowables to append to
            section_number: Section number (e.g., "2.1", "2.2")
            title: Subsection title
        """
        story.append(Paragraph(
            f"{section_number} {title}",
            self.styles['heading2']
        ))
    
    def add_paragraph(self, story: List, text: str):
        """
        Add a paragraph of body text to the report.
        
        Args:
            story: List of flowables to append to
            text: Paragraph text
        """
        story.append(Paragraph(text, self.styles['body']))
    
    def add_pfd_image(
        self,
        story: List,
        image_base64: str,
        caption: str = "Figure 1: Process Flow Diagram"
    ):
        """
        Add the PFD image to the report.
        
        Args:
            story: List of flowables to append to
            image_base64: Base64 encoded PNG image
            caption: Image caption
        """
        try:
            # Decode base64 image
            # Handle data URI format if present
            if ',' in image_base64:
                image_base64 = image_base64.split(',')[1]
            
            image_data = base64.b64decode(image_base64)
            image_buffer = io.BytesIO(image_data)
            
            # Get image dimensions using PIL
            pil_image = PILImage.open(image_buffer)
            img_width, img_height = pil_image.size
            
            # Reset buffer position
            image_buffer.seek(0)
            
            # Calculate scaled dimensions to fit page
            max_width = self.page_width - 2 * inch
            max_height = 5 * inch
            
            # Scale proportionally
            width_ratio = max_width / img_width
            height_ratio = max_height / img_height
            scale_ratio = min(width_ratio, height_ratio)
            
            scaled_width = img_width * scale_ratio
            scaled_height = img_height * scale_ratio
            
            # Create ReportLab Image
            img = Image(image_buffer, width=scaled_width, height=scaled_height)
            
            # Center the image
            img.hAlign = 'CENTER'
            
            story.append(img)
            story.append(Paragraph(caption, self.styles['caption']))
            
        except Exception as e:
            # If image fails, add error message
            story.append(Paragraph(
                f"[Error loading PFD image: {str(e)}]",
                self.styles['body']
            ))
    
    def add_stream_table(
        self,
        story: List,
        stream_data: StreamTableData,
        table_number: int = 1
    ):
        """
        Add stream table(s) to the report.
        
        Automatically splits wide tables across multiple pages/sections.
        
        Args:
            story: List of flowables to append to
            stream_data: StreamTableData object
            table_number: Starting table number for captions
        """
        if not stream_data.stream_ids:
            story.append(Paragraph(
                "No stream data available.",
                self.styles['body']
            ))
            return
        
        # Format the stream data into a 2D table
        formatted_table = self.table_formatter.format_stream_table(stream_data)
        
        # Split if necessary
        table_chunks = self.table_formatter.split_wide_table(formatted_table)
        
        for idx, chunk in enumerate(table_chunks):
            if len(table_chunks) > 1:
                caption = f"Table {table_number}: Stream Data - Mass Flows (Part {idx + 1} of {len(table_chunks)})"
            else:
                caption = f"Table {table_number}: Stream Data - Mass Flows"
            
            # Calculate column widths
            num_cols = len(chunk[0]) if chunk else 0
            if num_cols > 0:
                # First column wider for labels
                first_col_width = 1.5 * inch
                remaining_width = self.page_width - 2 * inch - first_col_width
                other_col_width = remaining_width / (num_cols - 1) if num_cols > 1 else remaining_width
                col_widths = [first_col_width] + [other_col_width] * (num_cols - 1)
            else:
                col_widths = None
            
            # Create table
            table = Table(chunk, colWidths=col_widths)
            table.setStyle(self._get_table_style(has_header=True))
            
            story.append(table)
            story.append(Paragraph(caption, self.styles['caption']))
            
            if idx < len(table_chunks) - 1:
                story.append(Spacer(1, 0.3 * inch))
    
    def add_equipment_table(
        self,
        story: List,
        equipment_data: EquipmentTableData,
        table_number: int = 2
    ):
        """
        Add equipment summary table to the report.
        
        Args:
            story: List of flowables to append to
            equipment_data: EquipmentTableData object
            table_number: Table number for caption
        """
        if not equipment_data.equipment_ids:
            story.append(Paragraph(
                "No equipment data available.",
                self.styles['body']
            ))
            return
        
        # Format the equipment data
        formatted_table = self.table_formatter.format_equipment_table(equipment_data)
        
        # Calculate column widths
        num_cols = len(formatted_table[0]) if formatted_table else 0
        if num_cols >= 4:
            col_widths = [2.5 * inch, 1.5 * inch, 1 * inch, 1 * inch]
        else:
            col_widths = None
        
        # Create table
        table = Table(formatted_table, colWidths=col_widths)
        table.setStyle(self._get_table_style(has_header=True))
        
        story.append(table)
        story.append(Paragraph(
            f"Table {table_number}: Equipment Operating Summary",
            self.styles['caption']
        ))
    
    def add_balance_summary(
        self,
        story: List,
        mass_balance: Optional[MassBalanceSummary],
        energy_balance: Optional[EnergyBalanceSummary]
    ):
        """
        Add mass and energy balance summary section.
        
        Args:
            story: List of flowables to append to
            mass_balance: MassBalanceSummary object (optional)
            energy_balance: EnergyBalanceSummary object (optional)
        """
        # Mass Balance subsection
        if mass_balance:
            self.add_subsection_header(story, "6.1", "Mass Balance")
            
            balance_data = [
                ["Property", "Value"],
                ["Total Mass In", f"{format_with_thousands_separator(mass_balance.total_mass_in, 'mass_flow')} kg/hr"],
                ["Total Mass Out", f"{format_with_thousands_separator(mass_balance.total_mass_out, 'mass_flow')} kg/hr"],
                ["Closure", f"{format_number(mass_balance.closure_percentage, 'efficiency')}%"],
            ]
            
            table = Table(balance_data, colWidths=[2 * inch, 2.5 * inch])
            table.setStyle(self._get_table_style(has_header=True))
            story.append(table)
            story.append(Spacer(1, 0.3 * inch))
        
        # Energy Balance subsection
        if energy_balance:
            self.add_subsection_header(story, "6.2", "Energy Balance")
            
            balance_data = [
                ["Property", "Value"],
                ["Total Heat Input", f"{format_with_thousands_separator(energy_balance.total_heat_input, 'duty')} kW"],
                ["Total Heat Output", f"{format_with_thousands_separator(energy_balance.total_heat_output, 'duty')} kW"],
            ]
            
            if energy_balance.total_power > 0:
                balance_data.append(
                    ["Total Power", f"{format_with_thousands_separator(energy_balance.total_power, 'duty')} kW"]
                )
            
            balance_data.append(
                ["Closure", f"{format_number(energy_balance.closure_percentage, 'efficiency')}%"]
            )
            
            table = Table(balance_data, colWidths=[2 * inch, 2.5 * inch])
            table.setStyle(self._get_table_style(has_header=True))
            story.append(table)
    
    def add_placeholder_section(self, story: List, section_number: str, title: str, placeholder_text: str):
        """
        Add a placeholder section for AI-generated content (Phase 2).
        
        Args:
            story: List of flowables to append to
            section_number: Section number
            title: Section title
            placeholder_text: Placeholder text to display
        """
        self.add_section_header(story, section_number, title)
        story.append(Paragraph(
            f"<i>{placeholder_text}</i>",
            self.styles['body']
        ))
        story.append(Spacer(1, 0.2 * inch))
    
    def generate_pdf(self, report_data: ReportData) -> bytes:
        """
        Generate the complete PDF report.
        
        Args:
            report_data: ReportData object with all report content
            
        Returns:
            PDF file as bytes
        """
        # Create PDF buffer
        buffer = io.BytesIO()
        
        # Create document
        doc = SimpleDocTemplate(
            buffer,
            pagesize=self.page_size,
            rightMargin=0.75 * inch,
            leftMargin=0.75 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
            title=report_data.metadata.title,
            author=report_data.metadata.author,
        )
        
        # Build story (list of flowables)
        story = []
        
        # 1. Cover Page
        self.add_cover_page(story, report_data.metadata)
        
        # 2. Table of Contents
        self.add_table_of_contents(story)
        
        # 3. Executive Summary
        self.add_section_header(story, "1", "Executive Summary")
        if report_data.executive_summary:
            self.add_paragraph(story, report_data.executive_summary)
        else:
            self.add_paragraph(story, 
                "<i>This section will contain an AI-generated executive summary of the simulation results, "
                "including key findings, process performance metrics, and overall conclusions. "
                "(AI narrative generation will be implemented in Phase 2)</i>"
            )
        story.append(Spacer(1, 0.3 * inch))
        
        # 4. Process Description
        self.add_section_header(story, "2", "Process Description")
        if report_data.process_description_sections:
            for section in report_data.process_description_sections:
                self.add_subsection_header(story, section.section_number, section.title)
                self.add_paragraph(story, section.content)
        else:
            self.add_paragraph(story,
                "<i>This section will contain AI-generated descriptions of each process unit, "
                "including operating conditions, inlet/outlet streams, and performance metrics. "
                "(AI narrative generation will be implemented in Phase 2)</i>"
            )
        story.append(PageBreak())
        
        # 5. Process Flow Diagram
        self.add_section_header(story, "3", "Process Flow Diagram")
        self.add_pfd_image(story, report_data.pfd_image_base64)
        story.append(PageBreak())
        
        # 6. Stream Tables
        self.add_section_header(story, "4", "Stream Tables")
        if report_data.stream_table:
            self.add_stream_table(story, report_data.stream_table, table_number=1)
        else:
            self.add_paragraph(story, "No stream data available.")
        story.append(PageBreak())
        
        # 7. Equipment Summary
        self.add_section_header(story, "5", "Equipment Summary")
        if report_data.equipment_table:
            self.add_equipment_table(story, report_data.equipment_table, table_number=2)
        else:
            self.add_paragraph(story, "No equipment data available.")
        story.append(Spacer(1, 0.5 * inch))
        
        # 8. Mass & Energy Balance
        self.add_section_header(story, "6", "Mass & Energy Balance")
        self.add_balance_summary(story, report_data.mass_balance, report_data.energy_balance)
        story.append(PageBreak())
        
        # 9. Observations & Conclusions
        self.add_section_header(story, "7", "Observations & Conclusions")
        if report_data.observations:
            self.add_paragraph(story, report_data.observations)
        else:
            self.add_paragraph(story,
                "<i>This section will contain AI-generated observations about the simulation results, "
                "including any anomalies, recommendations for optimization, and conclusions. "
                "(AI narrative generation will be implemented in Phase 2)</i>"
            )
        
        # Build the PDF
        doc.build(story)
        
        # Get PDF bytes
        pdf_bytes = buffer.getvalue()
        buffer.close()
        
        return pdf_bytes
