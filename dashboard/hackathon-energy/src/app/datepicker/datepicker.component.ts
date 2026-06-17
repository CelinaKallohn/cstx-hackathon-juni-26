import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { DateSelectionService } from '../services/date-selection.service';

@Component({
  selector: 'app-datepicker',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './datepicker.component.html',
  styleUrls: ['./datepicker.component.css'],
})
export class Datepicker {
  constructor(public ds: DateSelectionService) {}

  onPrev() { this.ds.prev(); }
  onNext() { this.ds.next(); }
  onDateChange(value: string) { this.ds.setDateIso(value); }
}


