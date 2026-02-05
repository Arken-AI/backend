"""
Report Generator Service

Main orchestration service for generating detailed simulation reports.
Coordinates data collection, table formatting, and PDF generation.

This service:
- Creates report records in MongoDB
- Manages report generation lifecycle
- Tracks progress and status
- Handles PDF storage and retrieval
"""

import asyncio
import uuid
import os
from typing import Optional
from datetime import datetime, timezone
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId

from app.config import settings
from app.models.report import (
    ReportStatus,
    ReportOptions,
    ReportRequest,
    ReportResponse,
    ReportStatusResponse,
    ReportMetadata,
    ReportData,
    StreamTableData,
    EquipmentTableData,
    MassBalanceSummary,
    EnergyBalanceSummary,
)
from app.services.report_data_collector import ReportDataCollector
from app.services.table_formatter import StreamTableFormatter
from app.services.pdf_generator import PDFReportGenerator
from app.services.narrative_generator import NarrativeGeneratorService


class ReportGeneratorService:
    """
    Orchestrates the complete report generation process.
    
    Workflow:
    1. Create report record (status: pending)
    2. Collect simulation data from MongoDB
    3. Format data into tables
    4. Generate PDF report
    5. Save PDF to storage
    6. Update record (status: completed, download_url)
    
    Progress tracking:
    - 0%: Started
    - 10%: Collecting data
    - 30%: Formatting tables
    - 50%: Generating PDF
    - 90%: Saving file
    - 100%: Complete
    """
    
    # Progress milestones
    PROGRESS_STARTED = 0
    PROGRESS_COLLECTING_DATA = 10
    PROGRESS_FORMATTING_TABLES = 30
    PROGRESS_GENERATING_AI = 50
    PROGRESS_GENERATING_PDF = 70
    PROGRESS_SAVING_FILE = 90
    PROGRESS_COMPLETE = 100
    
    def __init__(self, db: AsyncIOMotorDatabase):
        """
        Initialize the report generator service.
        
        Args:
            db: AsyncIOMotorDatabase instance for MongoDB operations
        """
        self.db = db
        self.reports_collection = db.reports
        
        # Initialize sub-services
        self.data_collector = ReportDataCollector(db)
        self.table_formatter = StreamTableFormatter(
            max_streams_per_table=settings.report_max_streams_per_table
        )
        self.pdf_generator = PDFReportGenerator(
            page_size=settings.report_pdf_page_size,
            max_streams_per_table=settings.report_max_streams_per_table
        )
        self.narrative_generator = NarrativeGeneratorService()
        
        # Ensure storage directory exists
        self.storage_path = Path(settings.report_storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
    
    async def generate_report(
        self,
        run_id: str,
        pfd_image_base64: str,
        options: Optional[ReportOptions] = None
    ) -> ReportResponse:
        """
        Start report generation process.
        
        Creates a report record and returns immediately.
        Actual generation happens in background.
        
        Args:
            run_id: ID of the simulation run
            pfd_image_base64: Base64 encoded PFD image
            options: Report generation options
            
        Returns:
            ReportResponse with report_id and initial status
        """
        # Use default options if not provided
        if options is None:
            options = ReportOptions()
        
        # Generate unique report ID
        report_id = str(uuid.uuid4())
        
        # Get current timestamp
        created_at = datetime.now(timezone.utc)
        
        # Create initial report record in MongoDB
        report_record = {
            "_id": report_id,
            "report_id": report_id,
            "run_id": run_id,
            "status": ReportStatus.PENDING.value,
            "progress": self.PROGRESS_STARTED,
            "current_step": "Initializing report generation",
            "options": options.model_dump(),
            "pfd_image_base64": pfd_image_base64,
            "created_at": created_at,
            "updated_at": created_at,
            "completed_at": None,
            "file_path": None,
            "download_url": None,
            "error": None,
        }
        
        # Insert record into MongoDB
        await self.reports_collection.insert_one(report_record)
        
        # Schedule background processing (will be triggered by API layer)
        # The API endpoint will call _process_report() as a background task
        
        # Estimate processing time (rough estimate: 10-30 seconds)
        estimated_time = 20
        
        # Return response immediately
        return ReportResponse(
            report_id=report_id,
            status=ReportStatus.PENDING,
            created_at=created_at,
            estimated_time_seconds=estimated_time,
        )
    
    async def _process_report(
        self,
        report_id: str,
        run_id: str,
        pfd_image_base64: str,
        options: ReportOptions
    ) -> None:
        """
        Process report generation in background.
        
        This method:
        1. Collects simulation data
        2. Formats tables
        3. Generates PDF (without AI content for now)
        4. Saves PDF to storage
        5. Updates report status
        
        Args:
            report_id: Unique report identifier
            run_id: Simulation run ID
            pfd_image_base64: Base64 encoded PFD image
            options: Report generation options
        """
        try:
            # ========================================
            # STEP 1: Update status to processing
            # ========================================
            await self._update_progress(
                report_id,
                self.PROGRESS_COLLECTING_DATA,
                "Collecting simulation data"
            )
            
            # ========================================
            # STEP 2: Collect simulation data from MongoDB
            # ========================================
            run_data = await self.data_collector.collect_run_data(run_id)
            
            # ========================================
            # STEP 3: Build stream table data
            # ========================================
            await self._update_progress(
                report_id,
                self.PROGRESS_FORMATTING_TABLES,
                "Formatting stream tables"
            )
            
            stream_table = None
            if options.include_stream_tables and run_data.streams:
                stream_table = self.data_collector.build_stream_table(run_data.streams)
            
            # ========================================
            # STEP 4: Build equipment table data
            # ========================================
            equipment_table = None
            if options.include_equipment_tables and run_data.equipment_list:
                equipment_table = self.data_collector.build_equipment_table(
                    run_data.equipment_list
                )
            
            # ========================================
            # STEP 5: Build mass balance summary
            # ========================================
            mass_balance = None
            if options.include_mass_balance:
                mass_balance = self.data_collector.build_mass_balance_summary(
                    run_data.calculation_results,
                    run_data.streams
                )
            
            # ========================================
            # STEP 6: Build energy balance summary
            # ========================================
            energy_balance = None
            if options.include_energy_balance:
                energy_balance = self.data_collector.build_energy_balance_summary(
                    run_data.calculation_results,
                    run_data.equipment_list
                )
            
            # ========================================
            # STEP 7: Generate AI narratives (if available)
            # ========================================
            executive_summary = None
            observations = None
            process_description_sections = None
            
            if options.include_ai_narratives and self.narrative_generator.is_available:
                await self._update_progress(
                    report_id,
                    self.PROGRESS_GENERATING_AI,
                    "Generating AI narratives"
                )
                
                # Generate executive summary
                executive_summary = await self.narrative_generator.generate_executive_summary(
                    run_data=run_data,
                    stream_table=stream_table,
                    equipment_table=equipment_table,
                    mass_balance=mass_balance,
                    energy_balance=energy_balance,
                )
                
                # Generate observations
                observations = await self.narrative_generator.generate_observations(
                    run_data=run_data,
                    mass_balance=mass_balance,
                    energy_balance=energy_balance,
                    equipment_table=equipment_table,
                    stream_table=stream_table,
                )
            
            # ========================================
            # STEP 8: Create report metadata
            # ========================================
            await self._update_progress(
                report_id,
                self.PROGRESS_GENERATING_PDF,
                "Generating PDF document"
            )
            
            metadata = ReportMetadata(
                run_id=run_id,
                process_name=run_data.process_name,
                title=f"{run_data.process_name} Simulation Report",
                date_generated=datetime.now(timezone.utc),
                author="ARKEN AI",
                version="1.0",
            )
            
            # ========================================
            # STEP 9: Assemble report data
            # ========================================
            report_data = ReportData(
                metadata=metadata,
                pfd_image_base64=pfd_image_base64,
                stream_table=stream_table,
                equipment_table=equipment_table,
                mass_balance=mass_balance,
                energy_balance=energy_balance,
                # AI-generated content
                executive_summary=executive_summary,
                process_description_sections=process_description_sections,
                observations=observations,
            )
            
            # ========================================
            # STEP 10: Generate PDF
            # ========================================
            pdf_bytes = self.pdf_generator.generate_pdf(report_data)
            
            # ========================================
            # STEP 11: Save PDF to storage
            # ========================================
            await self._update_progress(
                report_id,
                self.PROGRESS_SAVING_FILE,
                "Saving PDF file"
            )
            
            filename = self._generate_filename(report_id, run_data.process_name)
            file_path = await self._save_pdf_to_storage(pdf_bytes, filename)
            
            # ========================================
            # STEP 12: Update record as completed
            # ========================================
            download_url = f"/api/v1/reports/{report_id}/download"
            
            await self.reports_collection.update_one(
                {"_id": report_id},
                {
                    "$set": {
                        "status": ReportStatus.COMPLETED.value,
                        "progress": self.PROGRESS_COMPLETE,
                        "current_step": "Report generation complete",
                        "file_path": file_path,
                        "download_url": download_url,
                        "completed_at": datetime.now(timezone.utc),
                        "updated_at": datetime.now(timezone.utc),
                    }
                }
            )
            
        except Exception as e:
            # ========================================
            # ERROR HANDLING: Mark as failed
            # ========================================
            error_message = str(e)
            await self.reports_collection.update_one(
                {"_id": report_id},
                {
                    "$set": {
                        "status": ReportStatus.FAILED.value,
                        "current_step": "Report generation failed",
                        "error": error_message,
                        "updated_at": datetime.now(timezone.utc),
                    }
                }
            )
            # Re-raise for logging purposes (optional)
            raise
    
    async def get_report_status(self, report_id: str) -> Optional[ReportStatusResponse]:
        """
        Get current status of a report.
        
        Args:
            report_id: Unique report identifier
            
        Returns:
            ReportStatusResponse or None if not found
        """
        # Query MongoDB for the report record
        report = await self.reports_collection.find_one({"_id": report_id})
        
        if not report:
            return None
        
        # Build and return status response
        return ReportStatusResponse(
            report_id=report["report_id"],
            status=ReportStatus(report["status"]),
            progress=report.get("progress", 0),
            current_step=report.get("current_step", "Unknown"),
            download_url=report.get("download_url"),
            created_at=report["created_at"],
            completed_at=report.get("completed_at"),
            error=report.get("error"),
        )
    
    async def get_report_file(self, report_id: str) -> Optional[tuple[bytes, str]]:
        """
        Get the generated PDF file.
        
        Args:
            report_id: Unique report identifier
            
        Returns:
            Tuple of (pdf_bytes, filename) or None if not found/not ready
        """
        # Query MongoDB for the report record
        report = await self.reports_collection.find_one({"_id": report_id})
        
        if not report:
            return None
        
        # Check if report is completed
        if report.get("status") != ReportStatus.COMPLETED.value:
            return None
        
        # Get file path
        file_path = report.get("file_path")
        if not file_path:
            return None
        
        # Check if file exists
        full_path = Path(file_path)
        if not full_path.exists():
            return None
        
        # Read PDF file
        pdf_bytes = full_path.read_bytes()
        
        # Extract filename from path
        filename = full_path.name
        
        return (pdf_bytes, filename)
    
    async def _update_progress(
        self,
        report_id: str,
        progress: int,
        current_step: str,
        status: ReportStatus = ReportStatus.PROCESSING
    ) -> None:
        """
        Update report progress in MongoDB.
        
        Args:
            report_id: Unique report identifier
            progress: Progress percentage (0-100)
            current_step: Description of current step
            status: Report status
        """
        await self.reports_collection.update_one(
            {"_id": report_id},
            {
                "$set": {
                    "status": status.value,
                    "progress": progress,
                    "current_step": current_step,
                    "updated_at": datetime.now(timezone.utc),
                }
            }
        )
    
    def _generate_filename(self, report_id: str, process_name: str) -> str:
        """
        Generate a unique filename for the PDF.
        
        Format: {short_report_id}_{sanitized_process_name}_{timestamp}.pdf
        Example: abc123_Sugar_Mill_20260205_143022.pdf
        
        Args:
            report_id: Unique report identifier
            process_name: Name of the process
            
        Returns:
            Filename string
        """
        # Use first 8 chars of report_id for shorter filename
        short_id = report_id[:8]
        
        # Sanitize process name (replace spaces with underscores, remove special chars)
        import re
        sanitized_name = re.sub(r'[^a-zA-Z0-9_]', '', process_name.replace(' ', '_'))
        sanitized_name = sanitized_name[:30]  # Limit length
        
        # Generate timestamp
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        
        return f"{short_id}_{sanitized_name}_{timestamp}.pdf"
    
    async def _save_pdf_to_storage(
        self,
        pdf_bytes: bytes,
        filename: str
    ) -> str:
        """
        Save PDF to storage directory.
        
        Uses asyncio to run file I/O in thread pool to avoid blocking.
        
        Args:
            pdf_bytes: PDF file content
            filename: Filename to save as
            
        Returns:
            Full file path as string
        """
        file_path = self.storage_path / filename
        
        # Write file asynchronously using run_in_executor
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,  # Use default executor
            lambda: file_path.write_bytes(pdf_bytes)
        )
        
        return str(file_path)
    
    async def delete_report(self, report_id: str) -> bool:
        """
        Delete a report and its PDF file.
        
        Args:
            report_id: Unique report identifier
            
        Returns:
            True if deleted, False if not found
        """
        # Find the report first
        report = await self.reports_collection.find_one({"_id": report_id})
        
        if not report:
            return False
        
        # Delete the PDF file if it exists
        file_path = report.get("file_path")
        if file_path:
            path = Path(file_path)
            if path.exists():
                try:
                    path.unlink()  # Delete file
                except OSError:
                    pass  # Ignore file deletion errors
        
        # Delete the MongoDB record
        result = await self.reports_collection.delete_one({"_id": report_id})
        
        return result.deleted_count > 0
    
    async def cleanup_old_reports(self, days: int = 7) -> int:
        """
        Clean up reports older than specified days.
        
        Deletes both MongoDB records and PDF files.
        
        Args:
            days: Number of days to retain reports
            
        Returns:
            Number of reports deleted
        """
        from datetime import timedelta
        
        # Calculate cutoff date
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
        
        # Find all old reports
        cursor = self.reports_collection.find(
            {"created_at": {"$lt": cutoff_date}}
        )
        
        deleted_count = 0
        
        async for report in cursor:
            # Delete PDF file if exists
            file_path = report.get("file_path")
            if file_path:
                path = Path(file_path)
                if path.exists():
                    try:
                        path.unlink()
                    except OSError:
                        pass
            
            # Delete MongoDB record
            await self.reports_collection.delete_one({"_id": report["_id"]})
            deleted_count += 1
        
        return deleted_count
